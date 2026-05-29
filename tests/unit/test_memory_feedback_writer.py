from __future__ import annotations

from openakita.learning.feedback_writer import MemoryFeedbackWriter
from openakita.learning.models import LearningCase
from openakita.memory.types import MemoryType


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.saved = []

    def save_user_memory(self, memory):
        self.saved.append(memory)
        return memory.id


def test_feedback_writer_writes_high_severity_case() -> None:
    manager = _FakeMemoryManager()
    writer = MemoryFeedbackWriter(manager, min_repeat=2)
    case = LearningCase(
        source="self_check",
        source_ref="self_check:1:core_memory_001",
        severity="high",
        problem_summary="自检失败: core_memory_001",
        outcome_summary="memory broken",
        tags=["self_check", "anti_pattern"],
    )

    memory_ids = writer.write_case(case, repeat_count=1)

    assert len(memory_ids) == 1
    assert len(manager.saved) == 1
    assert manager.saved[0].type == MemoryType.ERROR
    assert "anti_pattern" in manager.saved[0].tags


def test_feedback_writer_skips_low_repeat_case() -> None:
    manager = _FakeMemoryManager()
    writer = MemoryFeedbackWriter(manager, min_repeat=3)
    case = LearningCase(
        source="self_check",
        source_ref="self_check:1:test_low",
        severity="low",
        problem_summary="自检失败: test_low",
        outcome_summary="minor",
    )

    memory_ids = writer.write_case(case, repeat_count=1)

    assert memory_ids == []
    assert manager.saved == []
