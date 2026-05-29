from __future__ import annotations

from pathlib import Path

from openakita.learning.models import LearningCase
from openakita.learning.promote import run_learning_promote
from openakita.learning.store import LearningStore


def test_run_learning_promote_applies_existing_dry_run_actions(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings
    from openakita.learning.executor import MinimalLearningExecutor

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:promote-1",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        candidate_actions=[
            {
                "action_id": "plan_promote_entry_1",
                "action_type": "tool_hint_patch",
                "target_path": "prompt/overrides/tool-hints.md",
                "title": "补充工具提示",
                "description": "加入工具限制警告",
                "rationale": ["missing_tool"],
                "constraints": ["仅白名单路径"],
                "details": {"suggested_patch_type": "tool_warning"},
            }
        ],
        root_cause="tool_limitation",
        harness_gap="missing_tool",
        tags=["failure_analysis", "missing_tool"],
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-1"], note="written", written=True)
    store.replace_candidate_actions(case_id, case.candidate_actions)

    executor = MinimalLearningExecutor(store=store)
    dry_run_result = executor.execute_action(case_id, case.candidate_actions[0], dry_run=True)

    summary = run_learning_promote(limit=10, store=store)
    action_record = store.get_action_record("plan_promote_entry_1")
    latest_run = store.get_latest_run("system:learning_promote")
    target_file = tmp_path / "prompt" / "overrides" / "tool-hints.md"

    assert dry_run_result.status == "dry_run"
    assert summary["status"] == "completed"
    assert summary["mode"] == "apply"
    assert summary["eligible_actions"] == 1
    assert summary["promoted_actions"] == 1
    assert summary["promoted_cases"] == 1
    assert summary["rejected_actions"] == 0
    assert summary["skipped_missing_case"] == 0
    assert summary["skipped_missing_action"] == 0
    assert action_record is not None
    assert action_record.status == "applied"
    assert target_file.exists()
    assert latest_run is not None
    assert latest_run.summary["promoted_actions"] == 1


def test_run_learning_promote_rejects_when_autofix_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings
    from openakita.learning.executor import MinimalLearningExecutor

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", False)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:promote-2",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        candidate_actions=[
            {
                "action_id": "plan_promote_entry_2",
                "action_type": "prompt_patch",
                "target_path": "prompt/overrides/learning-loop.md",
                "title": "降低循环",
                "description": "加入循环退出提示",
                "rationale": ["trace 出现循环"],
                "constraints": ["仅白名单路径"],
                "details": {"suggested_patch_type": "prompt_guardrail"},
            }
        ],
        tags=["failed", "loop"],
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-1"], note="written", written=True)
    store.replace_candidate_actions(case_id, case.candidate_actions)

    executor = MinimalLearningExecutor(store=store)
    executor.execute_action(case_id, case.candidate_actions[0], dry_run=True)

    summary = run_learning_promote(limit=10, store=store)
    action_record = store.get_action_record("plan_promote_entry_2")
    latest_run = store.get_latest_run("system:learning_promote")

    assert summary["eligible_actions"] == 1
    assert summary["promoted_actions"] == 0
    assert summary["rejected_actions"] == 1
    assert action_record is not None
    assert action_record.status == "rejected"
    assert not (tmp_path / "prompt" / "overrides" / "learning-loop.md").exists()
    assert latest_run is not None
    assert latest_run.summary["rejected_actions"] == 1
