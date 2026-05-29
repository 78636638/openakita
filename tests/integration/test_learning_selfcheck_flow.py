from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openakita.evolution.self_check import CheckReport, SelfChecker
from openakita.evolution.self_check import TestResult as SelfCheckTestResult
from openakita.learning.store import LearningStore


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.saved = []

    def save_user_memory(self, memory):
        self.saved.append(memory)
        return memory.id


@pytest.mark.asyncio
async def test_selfcheck_learning_flow_writes_case_and_memory(tmp_path: Path, monkeypatch) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_loop_enabled", True)
    monkeypatch.setattr(settings, "learning_min_repeat_for_memory_write", 2)

    manager = _FakeMemoryManager()
    checker = SelfChecker(brain=SimpleNamespace(), memory_manager=manager)
    report = CheckReport(
        timestamp=datetime(2026, 1, 1, 8, 0, 0),
        total_tests=1,
        passed=0,
        failed=1,
        results=[
            SelfCheckTestResult(
                test_id="core_memory_001",
                passed=False,
                error="memory db corrupted",
                duration_ms=10,
            )
        ],
    )

    await checker.learn_from_check(report)

    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    cases = store.list_cases(limit=10)

    assert len(cases) == 1
    assert cases[0].source == "self_check"
    assert len(manager.saved) == 1
    assert manager.saved[0].source == "learning_review"
