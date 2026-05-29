from __future__ import annotations

from typing import Any

from ..memory.types import MemoryPriority, MemoryType, SemanticMemory
from .models import LearningCase


class MemoryFeedbackWriter:
    def __init__(self, memory_manager: Any, *, min_repeat: int = 2) -> None:
        self.memory_manager = memory_manager
        self.min_repeat = max(1, int(min_repeat))

    def should_write(self, case: LearningCase, *, repeat_count: int = 1) -> bool:
        if not self.memory_manager:
            return False
        if case.severity == "high":
            return True
        if "user_correction" in case.tags:
            return True
        return repeat_count >= self.min_repeat

    def write_case(self, case: LearningCase, *, repeat_count: int = 1) -> list[str]:
        if not self.should_write(case, repeat_count=repeat_count):
            return []
        memory = self._build_memory(case, repeat_count=repeat_count)
        memory_id = self.memory_manager.save_user_memory(memory)
        return [memory_id] if memory_id else []

    def _build_memory(self, case: LearningCase, *, repeat_count: int) -> SemanticMemory:
        is_failure = case.case_type in {"failure", "near_miss", "regression"}
        mem_type = MemoryType.ERROR if is_failure else MemoryType.EXPERIENCE
        predicate = "避免重复错误" if is_failure else "复用成功套路"
        tags = ["learning_case", case.source, case.domain]
        tags.append("anti_pattern" if is_failure else "playbook")
        tags.extend(tag for tag in case.tags if tag not in tags)

        detail_lines = [case.problem_summary.strip()]
        if case.outcome_summary.strip() and case.outcome_summary.strip() != case.problem_summary.strip():
            detail_lines.append(f"结论: {case.outcome_summary.strip()}")
        if case.root_cause:
            detail_lines.append(f"根因: {case.root_cause}")
        if case.harness_gap:
            detail_lines.append(f"缺口: {case.harness_gap}")
        if case.evidence:
            detail_lines.append(f"证据: {case.evidence[0]}")
        detail_lines.append(f"重复次数: {repeat_count}")

        importance = 0.85 if case.severity == "high" else 0.65
        return SemanticMemory(
            type=mem_type,
            priority=MemoryPriority.LONG_TERM,
            content="；".join(x for x in detail_lines if x),
            source="learning_review",
            subject="系统学习",
            predicate=predicate,
            tags=tags,
            importance_score=importance,
        )
