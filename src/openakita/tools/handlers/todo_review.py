from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

PASS_MARKERS = ("审查通过", "通过审查", "验收通过", "全部通过", "review passed", "pass")

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


def build_review_retry_message(original_message: str) -> str:
    """
    构造重试时发送给 LLM 的消息：
    在原任务描述后追加 REVIEW_RETRY_PROMPT，强化 JSON 输出要求。
    """
    return original_message + REVIEW_RETRY_PROMPT
FAIL_MARKERS = (
    "未通过",
    "不通过",
    "审查失败",
    "验收失败",
    "存在问题",
    "存在缺陷",
    "待修复",
    "需修复",
    "需补充",
    "需要补充",
    "未完成",
    "存在幻觉",
    "存在欺骗",
    "说谎式完成",
)


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
) -> dict[str, Any]:
    text = str(result or "").strip()
    payload = _extract_json_payload(text)
    if isinstance(payload, dict):
        return _normalize_review_summary(
            payload,
            raw_result=text,
            requires_code_test=requires_code_test,
            requires_delivery=requires_delivery,
        )
    return _normalize_review_summary(
        _build_fallback_review_summary(text),
        raw_result=text,
        requires_code_test=requires_code_test,
        requires_delivery=requires_delivery,
    )


def build_next_round_todo(
    plan: dict[str, Any],
    *,
    blockers: list[str],
    review_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(review_summary or {})
    next_actions = _normalize_list(
        summary.get("next_actions")
        or summary.get("follow_up_actions")
        or summary.get("remediation_steps")
    )
    if not next_actions:
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
        steps.append(
            {
                "id": f"step_{index}",
                "description": str(action).strip()[:512],
                "depends_on": depends_on,
                "status": "pending",
            }
        )

    if not steps:
        steps = [{"id": "step_1", "description": "补充缺失证据并重新执行最终审查", "status": "pending"}]

    task_summary = str(plan.get("task_summary", "") or "当前任务").strip()[:200]
    return {
        "task_summary": f"{task_summary} - 审查未通过后的下一轮修复",
        "generated_from_plan_id": str(plan.get("id", "") or "").strip(),
        "source": "review_failure",
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
) -> dict[str, Any]:
    delivery_section = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
    tests_section = payload.get("tests") if isinstance(payload.get("tests"), dict) else {}

    summary = {
        "verdict": _normalize_verdict(
            payload.get("review_status")
            or payload.get("verdict")
            or payload.get("status")
            or payload.get("result")
            or payload.get("decision")
            or ("passed" if payload.get("passed") else "unknown")
        ),
        "passed": bool(payload.get("passed")) if "passed" in payload else None,
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
    if summary["passed"] is None:
        summary["passed"] = summary["verdict"] == "passed"
    if summary["verdict"] == "unknown" and summary["passed"]:
        summary["verdict"] = "passed"
    # 安全网：若 verdict 是 failed/needs_follow_up，强制 passed 为 False
    if summary["verdict"] in ("failed", "needs_follow_up") and summary["passed"] is True:
        summary["passed"] = False
        logger.debug(
            "[TodoReview] verdict=%s 与 passed=true 矛盾，强制 passed=False",
            summary["verdict"],
        )
    if summary["hallucination_found"] is True:
        summary["verdict"] = "failed"
        summary["passed"] = False
        if "发现幻觉式完成" not in summary["blockers"]:
            summary["blockers"].append("发现幻觉式完成")
    if requires_delivery and summary["delivery_verified"] is False:
        summary["blockers"].append("交付回执或交付物未核验通过")
    if summary["missing_deliverables"]:
        summary["blockers"].extend(
            [f"缺少交付物：{item}" for item in summary["missing_deliverables"] if str(item or "").strip()]
        )
    if summary["test_evidence"]["required"] and summary["test_evidence"]["verified"] is False:
        summary["blockers"].append("测试证据未核验通过")
    summary["blockers"] = list(dict.fromkeys(summary["blockers"]))
    summary["next_actions"] = list(dict.fromkeys(summary["next_actions"]))
    return summary


def _build_fallback_review_summary(text: str) -> dict[str, Any]:
    lowered = str(text or "").strip().lower()
    blockers: list[str] = []
    verdict = "unknown"
    if lowered:
        if any(marker.lower() in lowered for marker in PASS_MARKERS) and not any(
            marker.lower() in lowered for marker in FAIL_MARKERS
        ):
            verdict = "passed"
        elif any(marker.lower() in lowered for marker in FAIL_MARKERS):
            verdict = "failed"
            blockers.append(str(text).strip()[:240])

    delivery_verified = None
    if any(token in lowered for token in ("交付已核验", "回执已核验", "delivery verified")):
        delivery_verified = True
    elif any(token in lowered for token in ("交付未核验", "回执未核验", "缺少交付物")):
        delivery_verified = False

    test_verified = None
    if any(
        token in lowered
        for token in ("已运行 pytest", "已执行 pytest", "已完成测试", "已运行功能测试", "已做回归测试")
    ):
        test_verified = True
    elif any(token in lowered for token in ("未测试", "缺少测试", "没有测试", "未记录测试")):
        test_verified = False

    hallucination_found = None
    if "无幻觉" in text or "未发现幻觉" in text:
        hallucination_found = False
    elif "存在幻觉" in text or "幻觉式完成" in text:
        hallucination_found = True

    return {
        "verdict": verdict,
        "passed": verdict == "passed",
        "blockers": blockers,
        "delivery_verified": delivery_verified,
        "hallucination_found": hallucination_found,
        "test_verified": test_verified,
        "next_actions": [],
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
