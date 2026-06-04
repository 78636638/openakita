from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 审查步骤重试提示词：当 LLM 未返回有效 JSON 时追加
# 强化 LLM 输出 JSON 格式的指令，仅在 1 次重试时使用
REVIEW_RETRY_PROMPT = (
    "\n\n【重要 - 重新输出 JSON】"
    "你刚才的回复中未找到符合规范的 JSON 代码块。"
    "请仅输出严格符合下述格式的 JSON 代码块（不要任何其他说明性文字）：\n"
    "```json\n"
    "{\n"
    '  "review_status": "passed" | "failed" | "needs_follow_up",\n'
    '  "passed": true | false,\n'
    '  "blockers": [],\n'
    '  "delivery_verified": true | false,\n'
    '  "test_verified": true | false,\n'
    '  "hallucination_found": true | false,\n'
    '  "deliverables": [],\n'
    '  "missing_deliverables": [],\n'
    '  "summary": "一句话结论"\n'
    "}\n"
    "```\n"
    "系统将严格按 review_status 字段判定审查结果。"
)

# 审查步骤最大重试次数（避免无限重试）
REVIEW_MAX_RETRIES = 1


def is_valid_review_json(text: str) -> bool:
    """
    判断文本中是否包含有效的审查 JSON（含 review_status 字段）。

    用于审查步骤的 LLM 重试机制：若 LLM 未按规范返回 JSON，则触发重试。
    """
    payload = _extract_json_payload(text)
    if not isinstance(payload, dict):
        return False
    if "review_status" not in payload:
        return False
    status = str(payload.get("review_status", "")).strip().lower()
    return status in ("passed", "failed", "needs_follow_up")


def build_review_retry_message(
    original_message: str,
    *,
    previous_output: str | None = None,
    previous_failure_reason: str | None = None,
) -> str:
    """构造重试时发送给 LLM 的消息。

    2026-06 L8 增强：
      - 携带 LLM 上次输出（前 200 字符）作为 context
      - 携带上次失败原因（如"未找到 review_status 字段"）
      - 不再是"凭空"要求 LLM 重写

    Args:
        original_message: 原任务描述
        previous_output: LLM 上次返回的完整文本（会被截断到 200 字符）
        previous_failure_reason: 解析层报出的具体失败原因

    Returns:
        拼装后的重试消息
    """
    ctx_block = ""
    if previous_output or previous_failure_reason:
        ctx_parts: list[str] = []
        if previous_failure_reason:
            ctx_parts.append(f"上次失败原因：{previous_failure_reason}")
        if previous_output:
            truncated = str(previous_output).strip()[:200]
            if len(str(previous_output)) > 200:
                truncated += "…（已截断）"
            ctx_parts.append(f"你上次输出了：\n{truncated}")
        ctx_block = "\n\n" + "\n".join(ctx_parts) + "\n"

    return original_message + ctx_block + REVIEW_RETRY_PROMPT


def is_review_step(step: dict[str, Any] | None) -> bool:
    if not isinstance(step, dict):
        return False
    if str(step.get("kind", "") or "").strip().lower() == "review":
        return True
    description = str(step.get("description", "") or "").strip()
    return description.startswith("审查：")


def parse_review_result(
    result: str,
    *,
    requires_code_test: bool = False,
    requires_delivery: bool = False,
    trace: list[dict] | None = None,
) -> dict[str, Any]:
    """统一入口：raw text → ReviewSummary dict

    2026-06 L5（结构化日志）：
      - 各阶段事件写入 trace 列表（可选）
      - 最终调用一次 logger.debug 记录整个 trace
      - L1/L6 后续会消费 trace 决定重试/阻止关闭

    返回字段（向后兼容）：
      verdict, passed, blockers, next_actions, delivery_verified,
      hallucination_found, test_evidence, raw_result, requires_re_review
    """
    text = str(result or "").strip()
    events: list[dict] = [] if trace is None else trace
    events.append({"event": "input", "raw_length": len(text), "requires_code_test": requires_code_test, "requires_delivery": requires_delivery})

    payload = _extract_json_payload(text)
    if isinstance(payload, dict):
        events.append({"event": "json_extracted", "keys": sorted(payload.keys())})
        summary = _normalize_review_summary(
            payload,
            raw_result=text,
            requires_code_test=requires_code_test,
            requires_delivery=requires_delivery,
            _events=events,
        )
    else:
        events.append({"event": "fallback_used", "reason": "no_json_or_invalid"})
        summary = _normalize_review_summary(
            _build_fallback_review_summary(text),
            raw_result=text,
            requires_code_test=requires_code_test,
            requires_delivery=requires_delivery,
            _events=events,
        )

    # v0.2 关键字段：requires_re_review
    #   计算规则（与设计文档 4.3.1 一致）：
    #     passed is None                  → True（未生成有效结论）
    #     verdict == "failed" 且 blockers 非空 → True
    #     verdict == "needs_follow_up"     → True
    #   其它情况 → False
    verdict = summary.get("verdict")
    passed = summary.get("passed")
    blockers = summary.get("blockers") or []
    requires_re_review = (
        passed is None
        or (verdict == "failed" and bool(blockers))
        or verdict == "needs_follow_up"
    )
    summary["requires_re_review"] = requires_re_review
    events.append({
        "event": "summary_finalized",
        "verdict": verdict,
        "passed": passed,
        "requires_re_review": requires_re_review,
        "blocker_count": len(blockers),
    })

    logger.debug("[ReviewResolve] %s", events)
    return summary


def _is_review_not_concluded(blockers: list[str], review_summary: dict[str, Any]) -> bool:
    """2026-06 L9：判断"审查未生成有效结论"模式。

    当 LLM 根本没回结构化 JSON（passed is None / verdict=unknown）时，
    应当让 LLM **重做审查**而不是补 blocker；把 step_1 描述加上 [重做审查] 前缀。
    """
    if review_summary.get("passed") is None:
        return True
    if review_summary.get("verdict") == "unknown":
        return True
    for blocker in blockers or []:
        text = str(blocker or "")
        if "未生成" in text and "审查" in text:
            return True
    return False


def build_next_round_todo(
    plan: dict[str, Any],
    *,
    blockers: list[str],
    review_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(review_summary or {})
    review_not_concluded = _is_review_not_concluded(blockers, summary)

    next_actions = _normalize_list(
        summary.get("next_actions")
        or summary.get("follow_up_actions")
        or summary.get("remediation_steps")
    )

    # 2026-06 L9：审查未生成有效结论 → 只生成"重做审查"单一 step
    if review_not_concluded:
        next_actions = ["重做最终审查：按 JSON 格式输出 review_status / passed / blockers / next_actions"]
    elif not next_actions:
        next_actions = [f"修复问题：{item}" for item in blockers if str(item or "").strip()]
    if not next_actions:
        next_actions = ["补充缺失证据并重新执行最终审查"]

    steps: list[dict[str, Any]] = []
    for index, action in enumerate(next_actions, start=1):
        if not str(action or "").strip():
            continue
        depends_on = []
        if index > 1:
            depends_on = [f"step_{index - 1}"]
        description = str(action).strip()[:512]
        # 2026-06 L9：审查未生成有效结论 → step_1 显式标 [重做审查]
        if index == 1 and review_not_concluded and not description.startswith("[重做审查]"):
            description = f"[重做审查] {description}"
        steps.append(
            {
                "id": f"step_{index}",
                "description": description,
                "depends_on": depends_on,
                "status": "pending",
                # 2026-06 L9：标记 step 类型，方便编排层/LLM 识别
                "kind": "re_review" if (index == 1 and review_not_concluded) else "fix",
            }
        )

    if not steps:
        steps = [{"id": "step_1", "description": "补充缺失证据并重新执行最终审查", "status": "pending", "kind": "fix"}]

    task_summary = str(plan.get("task_summary", "") or "当前任务").strip()[:200]
    source = "review_re_review" if review_not_concluded else "review_failure"
    return {
        "task_summary": f"{task_summary} - {'审查未生成结论重做' if review_not_concluded else '审查未通过后的下一轮修复'}",
        "generated_from_plan_id": str(plan.get("id", "") or "").strip(),
        "source": source,
        "review_verdict": summary.get("verdict", "unknown"),
        "blockers": [str(item).strip() for item in blockers if str(item or "").strip()],
        "steps": steps,
    }


def summarize_review_summary(review_summary: dict[str, Any]) -> str:
    if not isinstance(review_summary, dict):
        return ""
    parts: list[str] = []
    verdict = str(review_summary.get("verdict", "") or "").strip()
    if verdict:
        parts.append(f"结论={verdict}")
    blockers = _normalize_list(review_summary.get("blockers"))
    if blockers:
        parts.append("阻塞项=" + "；".join(blockers[:3]))
    next_actions = _normalize_list(review_summary.get("next_actions"))
    if next_actions:
        parts.append("下一步=" + "；".join(next_actions[:2]))
    return " | ".join(parts)


def _extract_json_payload(content: str) -> Any | None:
    text = str(content or "").strip()
    if not text:
        return None
    direct = _try_parse_json(text)
    if direct is not None:
        return direct
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE):
        candidate = match.group(1).strip()
        parsed = _try_parse_json(candidate)
        if parsed is not None:
            return parsed
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _try_parse_json(candidate: str) -> Any | None:
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None


def _normalize_review_summary(
    payload: dict[str, Any],
    *,
    raw_result: str,
    requires_code_test: bool,
    requires_delivery: bool,
    _events: list[dict] | None = None,
) -> dict[str, Any]:
    delivery_section = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
    tests_section = payload.get("tests") if isinstance(payload.get("tests"), dict) else {}
    if _events is None:
        _events = []  # 内部 helper 可被独立调用，此时不收集 trace

    # 2026-06 P0-Bug-A 修复：必须区分 "passed 字段缺失" 与 "passed 显式为 None"。
    # 旧实现 `bool(payload.get("passed")) if "passed" in payload else None` 会把
    # passed=None（fallback 路径写进 payload 的值）变成 False，污染编排层判定。
    passed_raw = payload.get("passed")
    summary_passed: bool | None = (
        None if passed_raw is None else bool(passed_raw)
    )
    summary = {
        "verdict": _normalize_verdict(
            payload.get("review_status")
            or payload.get("verdict")
            or payload.get("status")
            or payload.get("result")
            or payload.get("decision")
            or ("passed" if payload.get("passed") else "unknown")
        ),
        "passed": summary_passed,
        "blockers": _normalize_list(
            payload.get("blockers")
            or payload.get("gaps")
            or payload.get("issues")
            or payload.get("missing_requirements")
        ),
        "next_actions": _normalize_list(
            payload.get("next_actions")
            or payload.get("follow_up_actions")
            or payload.get("remediation_steps")
            or payload.get("todos")
        ),
        "checked_step_ids": _normalize_list(
            payload.get("checked_step_ids")
            or payload.get("verified_step_ids")
            or payload.get("step_ids")
        ),
        "delivery_verified": _normalize_optional_bool(
            payload.get("delivery_verified")
            if "delivery_verified" in payload
            else delivery_section.get("verified")
        ),
        "deliverables": _normalize_list(
            payload.get("deliverables")
            or payload.get("artifacts")
            or payload.get("files")
            or delivery_section.get("deliverables")
        ),
        "missing_deliverables": _normalize_list(
            payload.get("missing_deliverables") or delivery_section.get("missing_deliverables")
        ),
        "hallucination_checked": _normalize_optional_bool(payload.get("hallucination_checked")),
        "hallucination_found": _normalize_optional_bool(payload.get("hallucination_found")),
        "test_evidence": {
            "required": bool(
                payload.get("test_required")
                if "test_required" in payload
                else requires_code_test
            ),
            "verified": _normalize_optional_bool(
                payload.get("test_verified")
                if "test_verified" in payload
                else tests_section.get("verified")
            ),
            "evidence_step_ids": _normalize_list(
                payload.get("test_evidence_step_ids")
                or tests_section.get("evidence_step_ids")
                or tests_section.get("step_ids")
            ),
            "evidence_tools": _normalize_list(
                payload.get("test_evidence_tools")
                or tests_section.get("evidence_tools")
                or tests_section.get("tools")
            ),
            "notes": str(
                payload.get("test_notes")
                or tests_section.get("notes")
                or payload.get("notes")
                or ""
            ).strip()[:500],
        },
        "raw_result": raw_result[:2000],
    }

    summary["requires_delivery"] = bool(requires_delivery)
    # 当 verdict="unknown"（即 LLM 没回结构化 JSON）时，passed 必须保持 None，
    # 不要反推为 False，否则会触发编排层"最终审查未明确通过"的旧分支。
    # 编排层会按 review_passed is None 单独走"审查未生成有效结论"分支。
    if summary["passed"] is None and summary["verdict"] != "unknown":
        summary["passed"] = summary["verdict"] == "passed"
    if summary["verdict"] == "unknown" and summary["passed"]:
        summary["verdict"] = "passed"
    # 安全网：若 verdict 是 failed/needs_follow_up，强制 passed 为 False
    if summary["verdict"] in ("failed", "needs_follow_up") and summary["passed"] is True:
        summary["passed"] = False
        _events.append({"event": "safety_net", "rule": "verdict_passed_contradiction",
                       "verdict": summary["verdict"]})
        logger.debug(
            "[TodoReview] verdict=%s 与 passed=true 矛盾，强制 passed=False",
            summary["verdict"],
        )
    if summary["hallucination_found"] is True:
        summary["verdict"] = "failed"
        summary["passed"] = False
        if "发现幻觉式完成" not in summary["blockers"]:
            summary["blockers"].append("发现幻觉式完成")
        _events.append({"event": "safety_net", "rule": "hallucination_found"})
    if requires_delivery and summary["delivery_verified"] is False:
        summary["blockers"].append("交付回执或交付物未核验通过")
        _events.append({"event": "safety_net", "rule": "delivery_not_verified"})
    if summary["missing_deliverables"]:
        _events.append({"event": "safety_net", "rule": "missing_deliverables",
                       "items": list(summary["missing_deliverables"])})
        summary["blockers"].extend(
            [f"缺少交付物：{item}" for item in summary["missing_deliverables"] if str(item or "").strip()]
        )
    if summary["test_evidence"]["required"] and summary["test_evidence"]["verified"] is False:
        summary["blockers"].append("测试证据未核验通过")
        _events.append({"event": "safety_net", "rule": "test_evidence_missing"})
    summary["blockers"] = list(dict.fromkeys(summary["blockers"]))
    summary["next_actions"] = list(dict.fromkeys(summary["next_actions"]))
    return summary


def _build_fallback_review_summary(text: str) -> dict[str, Any]:
    """Fallback review summary when LLM did not return structured JSON.

    2026-06 P0-Bug-A 修复（不再用硬编码字符串推断审查结论）：

    历史实现会把自然语言审查文本当成弱信号，按 "审查通过" / "审查失败" 等
    子串匹配来反推 verdict。线上曾误判：LLM 写 "审查完成。报告文件存在..."
    被当成"未匹配 PASS" → verdict="unknown" → passed=False → 报告里出现
    "最终审查未明确通过"；LLM 写 "飞书推送失败" 也会被当成"审查失败"。

    新实现：fallback 不再做字符串兜底，全部字段返回 unknown/None。
    编排层拿到 verdict="unknown" / passed=None 时会判定为
    「审查未生成有效结论」并生成下一轮 Todo 让 LLM 重新按 JSON 格式输出。
    """
    return {
        "verdict": "unknown",
        "passed": None,  # 关键：保留 None，让编排层走"未生成有效结论"分支
        "blockers": [],
        "delivery_verified": None,
        "hallucination_found": None,
        "test_verified": None,
        "next_actions": [
            "重新执行审查步骤，按标准 JSON 格式输出 review_status 字段"
        ],
    }


def _normalize_verdict(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"passed", "pass", "ok", "success", "completed", "通过", "已通过"}:
        return "passed"
    if raw in {"failed", "fail", "error", "rejected", "未通过", "失败"}:
        return "failed"
    if raw in {"needs_follow_up", "retry", "incomplete", "blocked"}:
        return "needs_follow_up"
    return "unknown"


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        return [stripped[:500]]
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        normalized.append(text[:500])
    return list(dict.fromkeys(normalized))


def _normalize_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"true", "1", "yes", "y", "ok", "passed"}:
        return True
    if raw in {"false", "0", "no", "n", "failed"}:
        return False
    return None
