from __future__ import annotations

import json
from pathlib import Path

from openakita.learning.scheduler_hooks import run_learning_ingest, run_learning_review
from openakita.learning.store import LearningStore
from openakita.memory.types import MemoryType


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.saved = []

    def save_user_memory(self, memory):
        self.saved.append(memory)
        return memory.id


def test_failure_analysis_json_flows_through_ingest_and_review(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_loop_enabled", True)
    monkeypatch.setattr(settings, "learning_ingest_enabled", True)
    monkeypatch.setattr(settings, "learning_min_repeat_for_memory_write", 2)

    analysis_dir = tmp_path / "data" / "failure_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": "task-e2e-001",
        "timestamp": "2026-01-03T10:00:00",
        "root_cause": "tool_limitation",
        "harness_gap": "missing_tool",
        "suggestion": "add a browser or crawler capability",
        "evidence": ["Exit reason: max_iterations", "Tool failed repeatedly"],
        "metrics": {
            "total_tokens": 60000,
            "total_iterations": 12,
            "total_tool_calls": 9,
            "elapsed_seconds": 120.5,
            "error_count": 2,
        },
        "raw_data": {
            "task_description": "抓取网页并整理结构化结果",
            "exit_reason": "max_iterations",
            "iterations": 12,
            "supervisor_event_count": 3,
        },
    }
    (analysis_dir / "task-e2e-001.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    scanned, inserted = run_learning_ingest()

    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    pending_before_review = store.list_pending_review(limit=10)

    assert scanned == 1
    assert inserted == 1
    assert len(pending_before_review) == 1
    assert pending_before_review[0].source == "failure_analysis"
    assert pending_before_review[0].severity == "high"
    assert pending_before_review[0].problem_summary == "抓取网页并整理结构化结果"

    manager = _FakeMemoryManager()
    store.record_run(
        run_id="eval-run-1",
        trigger_source="system:daily_evaluation",
        status="ok",
        summary={
            "credit_outcome": "harmful",
            "credit_score": -0.12,
            "credit_stats": {"by_type": {}, "top_credit": []},
        },
        started_at="2026-01-03T09:00:00",
        finished_at="2026-01-03T09:05:00",
    )
    store.record_run(
        run_id="shadow-run-1",
        trigger_source="system:learning_shadow",
        status="ok",
        summary={
            "mode": "shadow",
            "planned_cases": 1,
            "generated_actions": 1,
            "shadowed_actions": 1,
            "skipped_existing_records": 0,
        },
        started_at="2026-01-03T09:10:00",
        finished_at="2026-01-03T09:11:00",
    )
    store.record_run(
        run_id="promote-run-1",
        trigger_source="system:learning_promote",
        status="ok",
        summary={
            "mode": "apply",
            "eligible_actions": 1,
            "promoted_actions": 1,
            "promoted_cases": 1,
            "rejected_actions": 0,
            "skipped_missing_case": 0,
            "skipped_missing_action": 0,
            "applied_targets": ["prompt/overrides/tool-hints.md"],
        },
        started_at="2026-01-03T09:15:00",
        finished_at="2026-01-03T09:16:00",
    )
    store.record_run(
        run_id="verifier-run-1",
        trigger_source="system:learning_verifier",
        status="ok",
        summary={
            "verified_actions": 1,
            "rolled_back_actions": 0,
            "attempted_actions": 1,
            "verification_types": ["replay"],
            "limit": 10,
        },
        started_at="2026-01-03T09:20:00",
        finished_at="2026-01-03T09:21:00",
    )
    reviewed, written = run_learning_review(manager)
    pending_after_review = store.list_pending_review(limit=10)
    latest_run = store.get_latest_run("system:daily_learning_review")

    assert reviewed == 1
    assert written == 1
    assert pending_after_review == []
    assert len(manager.saved) == 1
    assert manager.saved[0].type == MemoryType.ERROR
    assert manager.saved[0].source == "learning_review"
    assert "failure_analysis" in manager.saved[0].tags
    assert latest_run is not None
    assert latest_run.summary["reviewed_cases"] == 1
    assert latest_run.summary["written_cases"] == 1
    assert latest_run.summary["cases_with_hits"] == 0
    assert latest_run.summary["total_case_hits"] == 0
    assert "hit_stats" in latest_run.summary
    assert "credit_stats" in latest_run.summary
    assert latest_run.summary["latest_credit_outcome"] == "harmful"
    assert latest_run.summary["latest_credit_score"] == -0.12
    assert latest_run.summary["latest_shadow_metrics"] is not None
    assert latest_run.summary["latest_shadow_metrics"]["mode"] == "shadow"
    assert latest_run.summary["latest_shadow_metrics"]["planned_cases"] == 1
    assert latest_run.summary["latest_shadow_metrics"]["generated_actions"] == 1
    assert latest_run.summary["latest_shadow_metrics"]["shadowed_actions"] == 1
    assert latest_run.summary["latest_shadow_run"] is not None
    assert latest_run.summary["latest_shadow_run"]["trigger_source"] == "system:learning_shadow"
    assert latest_run.summary["latest_shadow_run"]["summary"]["shadowed_actions"] == 1
    assert latest_run.summary["latest_promote_metrics"] is not None
    assert latest_run.summary["latest_promote_metrics"]["mode"] == "apply"
    assert latest_run.summary["latest_promote_metrics"]["eligible_actions"] == 1
    assert latest_run.summary["latest_promote_metrics"]["promoted_actions"] == 1
    assert latest_run.summary["latest_promote_run"] is not None
    assert latest_run.summary["latest_promote_run"]["trigger_source"] == "system:learning_promote"
    assert latest_run.summary["latest_verifier_metrics"] is not None
    assert latest_run.summary["latest_verifier_metrics"]["verified_actions"] == 1
    assert latest_run.summary["latest_verifier_metrics"]["rolled_back_actions"] == 0
    assert latest_run.summary["latest_verifier_metrics"]["verification_types"] == ["replay"]
    assert latest_run.summary["latest_verifier_run"] is not None
    assert latest_run.summary["latest_verifier_run"]["trigger_source"] == "system:learning_verifier"
