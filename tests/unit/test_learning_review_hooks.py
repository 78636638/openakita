from __future__ import annotations

from pathlib import Path

from openakita.learning.models import LearningCase
from openakita.learning.scheduler_hooks import run_learning_review
from openakita.learning.store import LearningStore


class _FakeMemoryManager:
    def save_user_memory(self, memory):
        return memory.id


def test_run_learning_review_includes_latest_shadow_promote_and_verifier_run_summary(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_min_repeat_for_memory_write", 1)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:review-shadow-1",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        root_cause="tool_limitation",
        harness_gap="missing_tool",
        tags=["failure_analysis", "missing_tool"],
    )
    case_id, _ = store.upsert_case(case)
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
        started_at="2026-01-04T05:30:00",
        finished_at="2026-01-04T05:31:00",
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
        started_at="2026-01-04T05:45:00",
        finished_at="2026-01-04T05:46:00",
    )
    store.record_run(
        run_id="verifier-run-1",
        trigger_source="system:learning_verifier",
        status="ok",
        summary={
            "verified_actions": 2,
            "rolled_back_actions": 1,
            "attempted_actions": 3,
            "verification_types": ["replay"],
            "limit": 10,
        },
        started_at="2026-01-04T05:40:00",
        finished_at="2026-01-04T05:41:00",
    )

    reviewed, written = run_learning_review(_FakeMemoryManager())
    latest_review = store.get_latest_run("system:daily_learning_review")

    assert reviewed == 1
    assert written == 1
    assert latest_review is not None
    assert latest_review.summary["latest_shadow_metrics"] is not None
    assert latest_review.summary["latest_shadow_metrics"]["run_id"] == "shadow-run-1"
    assert latest_review.summary["latest_shadow_metrics"]["mode"] == "shadow"
    assert latest_review.summary["latest_shadow_metrics"]["planned_cases"] == 1
    assert latest_review.summary["latest_shadow_metrics"]["shadowed_actions"] == 1
    assert latest_review.summary["latest_shadow_metrics"]["skipped_existing_records"] == 0
    assert latest_review.summary["latest_shadow_metrics"]["preview_target_count"] == 0
    assert latest_review.summary["latest_shadow_run"] is not None
    assert latest_review.summary["latest_shadow_run"]["run_id"] == "shadow-run-1"
    assert latest_review.summary["latest_shadow_run"]["status"] == "ok"
    assert latest_review.summary["latest_shadow_run"]["summary"]["mode"] == "shadow"
    assert latest_review.summary["latest_shadow_run"]["summary"]["shadowed_actions"] == 1
    assert latest_review.summary["latest_promote_metrics"] is not None
    assert latest_review.summary["latest_promote_metrics"]["run_id"] == "promote-run-1"
    assert latest_review.summary["latest_promote_metrics"]["mode"] == "apply"
    assert latest_review.summary["latest_promote_metrics"]["eligible_actions"] == 1
    assert latest_review.summary["latest_promote_metrics"]["promoted_actions"] == 1
    assert latest_review.summary["latest_promote_metrics"]["rejected_actions"] == 0
    assert latest_review.summary["latest_promote_metrics"]["applied_target_count"] == 1
    assert latest_review.summary["latest_promote_run"] is not None
    assert latest_review.summary["latest_promote_run"]["run_id"] == "promote-run-1"
    assert latest_review.summary["latest_promote_run"]["summary"]["promoted_actions"] == 1
    assert latest_review.summary["latest_verifier_metrics"] is not None
    assert latest_review.summary["latest_verifier_metrics"]["run_id"] == "verifier-run-1"
    assert latest_review.summary["latest_verifier_metrics"]["verified_actions"] == 2
    assert latest_review.summary["latest_verifier_metrics"]["rolled_back_actions"] == 1
    assert latest_review.summary["latest_verifier_metrics"]["attempted_actions"] == 3
    assert latest_review.summary["latest_verifier_metrics"]["verification_types"] == ["replay"]
    assert latest_review.summary["latest_verifier_run"] is not None
    assert latest_review.summary["latest_verifier_run"]["run_id"] == "verifier-run-1"
    assert latest_review.summary["latest_verifier_run"]["summary"]["verified_actions"] == 2


def test_run_learning_review_sets_shadow_promote_and_verifier_summary_none_when_absent(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_min_repeat_for_memory_write", 1)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:review-shadow-2",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        root_cause="tool_limitation",
        harness_gap="missing_tool",
        tags=["failure_analysis", "missing_tool"],
    )
    case_id, _ = store.upsert_case(case)

    reviewed, written = run_learning_review(_FakeMemoryManager())
    latest_review = store.get_latest_run("system:daily_learning_review")

    assert reviewed == 1
    assert written == 1
    assert latest_review is not None
    assert latest_review.summary["latest_shadow_metrics"] is None
    assert latest_review.summary["latest_shadow_run"] is None
    assert latest_review.summary["latest_promote_metrics"] is None
    assert latest_review.summary["latest_promote_run"] is None
    assert latest_review.summary["latest_verifier_metrics"] is None
    assert latest_review.summary["latest_verifier_run"] is None
