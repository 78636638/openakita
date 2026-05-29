from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from .models import LearningCase
from .store import LearningStore


def _new_action_id() -> str:
    return f"plan_{uuid4().hex[:12]}"


@dataclass(slots=True)
class LearningActionCandidate:
    action_type: str
    title: str
    description: str
    target_path: str
    source_case_id: str
    rationale: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=lambda: ["smoke"])
    details: dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=_new_action_id)
    risk_level: str = "low"
    execution_mode: str = "shadow"
    confidence: float = 0.6
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_record(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "title": self.title,
            "description": self.description,
            "target_path": self.target_path,
            "source_case_id": self.source_case_id,
            "rationale": list(self.rationale),
            "constraints": list(self.constraints),
            "verification": list(self.verification),
            "details": dict(self.details),
            "risk_level": self.risk_level,
            "execution_mode": self.execution_mode,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


class MinimalLearningPlanner:
    """Generate low-risk candidate actions from high-value learning cases."""

    def plan_case(self, case: LearningCase) -> list[dict[str, Any]]:
        if not self._is_high_value_case(case):
            return []

        actions: list[LearningActionCandidate] = []
        if self._needs_prompt_patch(case):
            actions.append(self._build_prompt_patch(case))
        if self._needs_tool_hint_patch(case):
            actions.append(self._build_tool_hint_patch(case))
        if self._needs_skill_backlog(case):
            actions.append(self._build_skill_backlog(case))
        return [action.to_record() for action in self._deduplicate(actions)]

    def plan_cases(self, cases: list[LearningCase]) -> dict[str, list[dict[str, Any]]]:
        return {case.case_id: self.plan_case(case) for case in cases}

    @staticmethod
    def _is_high_value_case(case: LearningCase) -> bool:
        if case.severity == "high":
            return True
        if case.domain == "evaluation":
            judge_score = float(case.metrics.get("judge_score", 1.0) or 1.0)
            if judge_score < 0.6:
                return True
        return bool(case.tags and any(tag in {"anti_pattern", "loop", "failed"} for tag in case.tags))

    @staticmethod
    def _needs_prompt_patch(case: LearningCase) -> bool:
        metrics = case.metrics or {}
        text = " ".join(
            x for x in [case.problem_summary, case.outcome_summary, *(case.evidence or [])] if x
        ).lower()
        return (
            case.domain == "evaluation"
            and (
                bool(metrics.get("loop_detected"))
                or int(metrics.get("total_iterations", 0) or 0) >= 15
                or float(metrics.get("judge_score", 1.0) or 1.0) < 0.6
                or "循环" in text
                or "iteration" in text
            )
        )

    @staticmethod
    def _needs_tool_hint_patch(case: LearningCase) -> bool:
        values = " ".join(
            str(x).lower()
            for x in [
                case.root_cause or "",
                case.harness_gap or "",
                *(case.tags or []),
                case.problem_summary,
                case.outcome_summary,
            ]
        )
        tool_errors = int(case.metrics.get("tool_errors", 0) or 0)
        return (
            "tool" in values
            or "missing_tool" in values
            or "tool_limitation" in values
            or tool_errors > 0
        )

    @staticmethod
    def _needs_skill_backlog(case: LearningCase) -> bool:
        values = " ".join(
            str(x).lower()
            for x in [
                case.root_cause or "",
                case.harness_gap or "",
                *(case.tags or []),
            ]
        )
        suggestions = case.lineage.get("judge_suggestions", []) if case.lineage else []
        return (
            "skill" in values
            or "knowledge" in values
            or "capability" in values
            or bool(suggestions)
        )

    @staticmethod
    def _build_prompt_patch(case: LearningCase) -> LearningActionCandidate:
        return LearningActionCandidate(
            action_type="prompt_patch",
            title="降低循环与无效迭代",
            description=f"基于案例 {case.case_id} 生成提示词修正候选，减少循环或低效推理。",
            target_path="prompt/overrides/learning-loop.md",
            source_case_id=case.case_id,
            rationale=[
                case.problem_summary or "评估发现推理质量下降",
                case.outcome_summary or "需要补充推理约束",
            ],
            constraints=[
                "仅允许生成候选 patch，不直接执行",
                "不得修改 src/openakita/core/",
            ],
            details={
                "domain": case.domain,
                "severity": case.severity,
                "suggested_patch_type": "prompt_guardrail",
            },
            confidence=0.72,
        )

    @staticmethod
    def _build_tool_hint_patch(case: LearningCase) -> LearningActionCandidate:
        return LearningActionCandidate(
            action_type="tool_hint_patch",
            title="补充工具使用提示",
            description=f"基于案例 {case.case_id} 生成工具提示修正候选，减少错误选型或工具限制误用。",
            target_path="prompt/overrides/tool-hints.md",
            source_case_id=case.case_id,
            rationale=[
                case.root_cause or case.problem_summary or "出现工具相关失败模式",
                case.harness_gap or case.outcome_summary or "需要补充工具边界说明",
            ],
            constraints=[
                "仅允许修改提示词片段",
                "不得直接修改 tools handler 代码",
            ],
            details={
                "domain": case.domain,
                "severity": case.severity,
                "suggested_patch_type": "tool_warning",
            },
            confidence=0.74,
        )

    @staticmethod
    def _build_skill_backlog(case: LearningCase) -> LearningActionCandidate:
        suggestions = case.lineage.get("judge_suggestions", []) if case.lineage else []
        return LearningActionCandidate(
            action_type="skill_backlog_create",
            title="登记技能改进待办",
            description=f"基于案例 {case.case_id} 生成技能改进 backlog 候选，不直接创建正式技能。",
            target_path="skills/backlog/",
            source_case_id=case.case_id,
            rationale=[
                case.problem_summary or "存在能力缺口",
                *(suggestions[:2] or [case.outcome_summary or "需要登记后续技能改进"]),
            ],
            constraints=[
                "仅生成 backlog 候选，不直接发布技能",
                "需要后续 executor/verifier 再处理",
            ],
            details={
                "domain": case.domain,
                "severity": case.severity,
                "suggested_patch_type": "skill_backlog",
            },
            confidence=0.68,
        )

    @staticmethod
    def _deduplicate(
        actions: list[LearningActionCandidate],
    ) -> list[LearningActionCandidate]:
        deduped: list[LearningActionCandidate] = []
        seen: set[tuple[str, str]] = set()
        for action in actions:
            key = (action.action_type, action.target_path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)
        return deduped


def run_learning_planner(*, limit: int = 100, store: LearningStore | None = None) -> dict[str, int]:
    current_store = store or LearningStore()
    planner = MinimalLearningPlanner()
    planned_cases = 0
    generated_actions = 0

    for case in current_store.list_cases_for_planning(limit=limit):
        actions = planner.plan_case(case)
        if not actions:
            continue
        current_store.replace_candidate_actions(case.case_id, actions)
        planned_cases += 1
        generated_actions += len(actions)

    return {
        "planned_cases": planned_cases,
        "generated_actions": generated_actions,
    }
