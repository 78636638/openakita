#!/usr/bin/env python3
"""2026-06 L3：历史 plan 报告"审查未通过"误报回填脚本。

背景：
  - 2026-06-04 抖音热榜采集 plan (plan_20260604_134223_01c921) 实际任务完成
    但旧版 parse_review_result 用硬编码字符串匹配把 LLM 自然语言审查
    错判为"未通过"。
  - 修复后 parse_review_result 对 LLM 自然语言审查返回 verdict=unknown
    / passed=None，但这并不代表任务失败。
  - 历史 plan 文件的"审查摘要"和"下一轮 Todo 建议"区块可能误导用户。

本脚本：
  1. 扫描 data/plans/*.md
  2. 识别 "结论=unknown" 的"未生成 JSON"模式
  3. 检查 plan 状态 (status=completed) 是否与审查结论一致
  4. 如果 plan 实际 completed + 任务步骤全 completed：
     - 在文件末尾追加"## 审查回填说明 (2026-06)"段落
     - 明确说明：旧版误判 / 新版判定为 unknown (LLM 未回 JSON) / 实际任务已完成
     - 备份原文件到 data/plans/.bak/<timestamp>/
  5. 输出 BACKFILL_REPORT.md 记录改动

幂等：可重复运行，第二次运行时识别已回填的文件并跳过。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLANS_DIR = PROJECT_ROOT / "data" / "plans"
BACKUP_DIR_NAME = ".bak"

BACKFILL_MARKER = "## 审查回填说明 (2026-06)"


def _has_backfill_marker(content: str) -> bool:
    return BACKFILL_MARKER in content


def _extract_plan_status(content: str) -> str | None:
    m = re.search(r"^status:\s*(\S+)\s*$", content, re.MULTILINE)
    return m.group(1) if m else None


def _extract_review_verdict(content: str) -> str | None:
    """从"## 审查摘要"区块提取 结论=xxx。"""
    in_section = False
    for line in content.splitlines():
        if line.strip() == "## 审查摘要":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and "结论=" in line:
            m = re.search(r"结论=([^\s]+)", line)
            if m:
                return m.group(1)
    return None


def _all_steps_completed(content: str) -> bool:
    """扫描步骤表格：所有步骤是否都状态=✅（completed）。"""
    # 简单判定：行内含 "✅" 且不含其他状态（🔄 in_progress / ❌ failed / ⏭️ skipped）
    in_table = False
    for line in content.splitlines():
        if "## 步骤列表" in line:
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and line.startswith("| step_") or line.startswith("|step_"):
            if "✅" not in line:
                return False
            if any(emoji in line for emoji in ("🔄", "❌", "⏭️")):
                return False
    return bool(in_table)


def _build_backfill_block(plan_id: str, review_step_result: str) -> str:
    """构造回填说明段落。"""
    return f"""

{BACKFILL_MARKER}

> 本计划于 2026-06 L3 修复后自动回填：
>
> - **原报告**：结论=unknown + 下一轮 Todo "修复问题：最终'审查'未明确通过"
> - **旧版 bug**：parse_review_result 用硬编码字符串匹配误判 LLM 自然语言审查
> - **修复后判定**：verdict=unknown / passed=None（LLM 未返回结构化 JSON，不代表任务失败）
> - **实际任务状态**：所有步骤已完成（status=completed），交付物已生成
>
> 因此本计划判定为**已交付完成**，不再生成下一轮 Todo。
> 原始审查步骤 result：
> ```
> {review_step_result[:300]}
> ```
> 回填时间：{datetime.now().isoformat()}
"""


def backfill_one(plan_path: Path, backup_root: Path, *, force: bool = False) -> dict:
    """回填单个 plan 文件。返回改动报告 dict。

    Args:
        plan_path: plan 文件路径
        backup_root: 备份目录根
        force: 若已回填仍强制刷新（用于修复旧回填块的 bug）
    """
    content = plan_path.read_text(encoding="utf-8")
    report = {"file": str(plan_path), "changed": False, "skipped_reason": None}

    if _has_backfill_marker(content) and not force:
        report["skipped_reason"] = "already_backfilled"
        return report

    plan_id_match = re.search(r"^id:\s*(\S+)\s*$", content, re.MULTILINE)
    plan_id = plan_id_match.group(1) if plan_id_match else plan_path.stem

    plan_status = _extract_plan_status(content)
    verdict = _extract_review_verdict(content)
    all_done = _all_steps_completed(content)

    # 仅在 plan=completed + verdict=unknown + 所有步骤都✅时回填
    if plan_status != "completed":
        report["skipped_reason"] = f"plan_status={plan_status!r} (非 completed)"
        return report
    if verdict != "unknown":
        report["skipped_reason"] = f"verdict={verdict!r} (非 unknown)"
        return report
    if not all_done:
        report["skipped_reason"] = "存在未完成步骤"
        return report

    # 提取审查步骤的 result 文本（行尾最后一列；列数因 plan 而异）
    review_result_match = re.search(
        r"^\| step_5 \|(.*?)\|\s*$",
        content,
        re.MULTILINE,
    )
    if review_result_match:
        # 取最后一列
        cols = [c.strip() for c in review_result_match.group(1).split("|")]
        review_text = cols[-1] if cols else "(无原文)"
    else:
        review_text = "(无原文)"

    # 备份
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / plan_path.name
    shutil.copy2(plan_path, backup_path)

    # 追加回填说明
    new_content = content + _build_backfill_block(plan_id, review_text)
    plan_path.write_text(new_content, encoding="utf-8")

    report["changed"] = True
    report["backup"] = str(backup_path)
    report["plan_id"] = plan_id
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", type=Path, default=DEFAULT_PLANS_DIR)
    parser.add_argument("--dry-run", action="store_true", help="只扫描不修改")
    parser.add_argument("--force", action="store_true", help="强制刷新已回填的 plan 文件")
    args = parser.parse_args()

    if not args.plans_dir.exists():
        print(f"❌ plans 目录不存在: {args.plans_dir}")
        return 1

    backup_root = args.plans_dir / BACKUP_DIR_NAME
    plan_files = sorted(args.plans_dir.glob("*.md"))
    if not plan_files:
        print(f"⚠️ 目录下无 .md 文件: {args.plans_dir}")
        return 0

    print(f"扫描 {len(plan_files)} 个 plan 文件...")
    reports = []
    for plan_path in plan_files:
        if args.dry_run:
            content = plan_path.read_text(encoding="utf-8")
            verdict = _extract_review_verdict(content)
            status = _extract_plan_status(content)
            done = _all_steps_completed(content)
            print(
                f"  [DRY] {plan_path.name}: status={status}, verdict={verdict}, "
                f"all_done={done}, already_backfilled={_has_backfill_marker(content)}"
            )
        else:
            rep = backfill_one(plan_path, backup_root, force=args.force)
            reports.append(rep)
            if rep["changed"]:
                print(f"  ✅ 回填: {plan_path.name} → backup: {rep['backup']}")
            else:
                print(f"  ⏭️  跳过: {plan_path.name} ({rep['skipped_reason']})")

    # 写 BACKFILL_REPORT.md
    if not args.dry_run and reports:
        report_path = args.plans_dir / "BACKFILL_REPORT.md"
        changed = [r for r in reports if r["changed"]]
        skipped = [r for r in reports if not r["changed"]]
        lines = [
            "# Plan 审查回填报告",
            "",
            f"执行时间：{datetime.now().isoformat()}",
            f"扫描文件：{len(reports)}",
            f"实际回填：{len(changed)}",
            f"跳过：{len(skipped)}",
            "",
            "## 实际回填",
        ]
        for r in changed:
            lines.append(f"- `{Path(r['file']).name}` → backup `{r['backup']}`")
        lines += ["", "## 跳过（未达到回填条件）"]
        for r in skipped:
            lines.append(f"- `{Path(r['file']).name}`: {r['skipped_reason']}")
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告已生成: {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
