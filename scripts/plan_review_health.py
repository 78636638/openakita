#!/usr/bin/env python3
"""2026-06 L10：plan review 健康度监控。

扫描 data/plans/*.md 统计 4 项指标：
  1. plan 总数（最近 N 天）
  2. 触发 next_round_todo 的占比
  3. 审查 verdict 分布 (passed / failed / unknown / needs_follow_up)
  4. 误报占比：verdict=unknown 但 plan status=completed + 步骤全 ✅

输出 markdown 报告到 docs/plan-review-health/<week>.md。
支持 --days N 控制扫描窗口（默认 7）。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLANS_DIR = PROJECT_ROOT / "data" / "plans"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "plan-review-health"


def _extract_status(content: str) -> str | None:
    m = re.search(r"^status:\s*(\S+)\s*$", content, re.MULTILINE)
    return m.group(1) if m else None


def _extract_created_at(content: str) -> str | None:
    m = re.search(r"^created_at:\s*(\S+)\s*$", content, re.MULTILINE)
    return m.group(1) if m else None


def _extract_verdict(content: str) -> str | None:
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


def _has_next_round_section(content: str) -> bool:
    """是否存在 "## 下一轮 Todo 建议" 区块且非占位（不在回填说明内）。"""
    in_section = False
    for line in content.splitlines():
        if line.strip() == "## 下一轮 Todo 建议":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and "step_1:" in line and "修复" in line:
            return True
    return False


def _all_steps_completed(content: str) -> bool:
    """步骤表格中所有 step 行都含 ✅。"""
    in_table = False
    for line in content.splitlines():
        if "## 步骤列表" in line:
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and (line.startswith("| step_") or line.startswith("|step_")):
            if "✅" not in line:
                return False
            if any(e in line for e in ("🔄", "❌", "⏭️")):
                return False
    return in_table


def _is_false_unknown(verdict: str, content: str) -> bool:
    """verdict=unknown 但 plan 实际完成 → 误报。"""
    if verdict != "unknown":
        return False
    if _extract_status(content) != "completed":
        return False
    return _all_steps_completed(content)


def scan_plans(plans_dir: Path, *, days: int) -> dict:
    """扫描 plans 目录，返回统计 dict。"""
    cutoff = datetime.now() - timedelta(days=days)
    total = 0
    skipped_old = 0
    next_round_count = 0
    verdicts: Counter = Counter()
    false_unknown_count = 0
    samples: list[dict] = []

    for plan_path in sorted(plans_dir.glob("*.md")):
        # 排除 L3 脚本生成的报告与回填文件
        if plan_path.name in ("BACKFILL_REPORT.md",):
            continue
        content = plan_path.read_text(encoding="utf-8")
        created_at_str = _extract_created_at(content)
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if created_at < cutoff:
                    skipped_old += 1
                    continue
            except ValueError:
                pass  # 无法解析则纳入统计

        total += 1
        verdict = _extract_verdict(content) or "(无审查摘要)"
        verdicts[verdict] += 1
        if _has_next_round_section(content):
            next_round_count += 1
        if _is_false_unknown(verdict, content):
            false_unknown_count += 1
            samples.append({"file": plan_path.name, "reason": "verdict=unknown + status=completed + 步骤全 ✅"})

    return {
        "window_days": days,
        "total": total,
        "skipped_old": skipped_old,
        "next_round_count": next_round_count,
        "next_round_ratio": next_round_count / total if total else 0.0,
        "verdicts": dict(verdicts),
        "false_unknown_count": false_unknown_count,
        "false_unknown_ratio": false_unknown_count / total if total else 0.0,
        "samples": samples,
    }


def render_markdown(stats: dict) -> str:
    lines = [
        f"# Plan Review 健康度报告 ({datetime.now().strftime('%Y-%m-%d')})",
        "",
        f"**扫描窗口**：最近 {stats['window_days']} 天",
        f"**总 plan 数**：{stats['total']}（跳过 {stats['skipped_old']} 个超出窗口）",
        "",
        "## 1. 触发 next_round_todo 的占比",
        f"- 数量：{stats['next_round_count']}",
        f"- 占比：**{stats['next_round_ratio']:.1%}**",
        "",
        "## 2. 审查 verdict 分布",
        "| Verdict | 数量 | 占比 |",
        "|---------|------|------|",
    ]
    for verdict, count in sorted(stats["verdicts"].items(), key=lambda x: -x[1]):
        ratio = count / stats["total"] if stats["total"] else 0
        lines.append(f"| `{verdict}` | {count} | {ratio:.1%} |")

    lines += [
        "",
        "## 3. 误报占比（verdict=unknown + plan 已完成）",
        f"- 数量：{stats['false_unknown_count']}",
        f"- 占比：**{stats['false_unknown_ratio']:.1%}**",
        "",
    ]
    if stats["samples"]:
        lines.append("### 误报样本（建议人工/脚本回填）")
        for s in stats["samples"]:
            lines.append(f"- `{s['file']}`: {s['reason']}")
    else:
        lines.append("✅ 最近窗口无 verdict=unknown 误报")

    lines += [
        "",
        "## 4. 健康度指标（自评）",
        "- 🟢 误报占比 < 5%：健康",
        "- 🟡 误报占比 5%-20%：观察，需要持续回填历史 plan",
        "- 🔴 误报占比 > 20%：review pipeline 有严重问题，需排查 parse_review_result / finalize_plan",
        "",
        f"_本报告由 `scripts/plan_review_health.py` 自动生成于 {datetime.now().isoformat()}_",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", type=Path, default=DEFAULT_PLANS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not args.plans_dir.exists():
        print(f"❌ plans 目录不存在: {args.plans_dir}")
        return 1

    stats = scan_plans(args.plans_dir, days=args.days)
    report_md = render_markdown(stats)

    if not args.quiet:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        week_str = datetime.now().strftime("%Y-W%V")
        out_path = args.output_dir / f"{week_str}.md"
        out_path.write_text(report_md, encoding="utf-8")
        print(f"✅ 报告: {out_path}")
        print()
        print(report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
