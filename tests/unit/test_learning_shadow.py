from __future__ import annotations

from pathlib import Path

from openakita.learning.models import LearningCase
from openakita.learning.shadow import run_learning_shadow
from openakita.learning.store import LearningStore


def test_run_learning_shadow_plans_and_dry_runs_without_writing(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:shadow-1",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        root_cause="tool_limitation",
        harness_gap="missing_tool",
        tags=["failure_analysis", "missing_tool"],
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-1"], note="written", written=True)

    summary = run_learning_shadow(limit=10, store=store)
    planned_case = store.get_case(case_id)
    assert planned_case is not None

    action_id = planned_case.candidate_actions[0]["action_id"]
    action_record = store.get_action_record(action_id)
    latest_run = store.get_latest_run("system:learning_shadow")

    assert summary["status"] == "completed"
    assert summary["mode"] == "shadow"
    assert summary["planned_cases"] == 1
    assert summary["generated_actions"] == 1
    assert summary["candidate_cases_total"] == 1
    assert summary["pending_cases"] == 1
    assert summary["pending_actions"] == 1
    assert summary["shadowed_cases"] == 1
    assert summary["shadowed_actions"] == 1
    assert summary["rejected_actions"] == 0
    assert summary["skipped_existing_records"] == 0
    assert action_record is not None
    assert action_record.status == "dry_run"
    assert latest_run is not None
    assert latest_run.summary["shadowed_actions"] == 1
    assert not (tmp_path / "prompt" / "overrides" / "tool-hints.md").exists()


def test_run_learning_shadow_skips_actions_with_existing_records(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:shadow-2",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        root_cause="tool_limitation",
        harness_gap="missing_tool",
        tags=["failure_analysis", "missing_tool"],
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-1"], note="written", written=True)

    first_summary = run_learning_shadow(limit=10, store=store)
    second_summary = run_learning_shadow(limit=10, store=store)
    latest_run = store.get_latest_run("system:learning_shadow")

    assert first_summary["shadowed_actions"] == 1
    assert second_summary["planned_cases"] == 0
    assert second_summary["pending_cases"] == 0
    assert second_summary["pending_actions"] == 0
    assert second_summary["shadowed_cases"] == 0
    assert second_summary["shadowed_actions"] == 0
    assert second_summary["skipped_existing_records"] == 1
    assert latest_run is not None
    assert latest_run.summary["skipped_existing_records"] == 1
    assert not (tmp_path / "prompt" / "overrides" / "tool-hints.md").exists()
