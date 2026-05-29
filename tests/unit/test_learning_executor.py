from __future__ import annotations

from pathlib import Path

from openakita.learning.executor import MinimalLearningExecutor, run_learning_executor
from openakita.learning.models import LearningCase
from openakita.learning.store import LearningStore


def test_minimal_learning_executor_dry_run_records_checkpoint_without_writing(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)

    result = executor.execute_action(
        "case-1",
        {
            "action_id": "plan_a1",
            "action_type": "prompt_patch",
            "target_path": "prompt/overrides/learning-loop.md",
            "title": "降低循环",
            "description": "预览提示词修正",
            "rationale": ["trace 出现循环"],
            "constraints": ["仅预览"],
            "details": {"suggested_patch_type": "prompt_guardrail"},
        },
        dry_run=True,
    )

    action_record = store.get_action_record("plan_a1")
    checkpoint = store.get_checkpoint(result.checkpoint_id)

    assert result.status == "dry_run"
    assert action_record is not None
    assert action_record.status == "dry_run"
    assert checkpoint is not None
    assert checkpoint.exists_before is False
    assert not (tmp_path / "prompt" / "overrides" / "learning-loop.md").exists()


def test_minimal_learning_executor_rejects_non_whitelisted_targets(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)

    result = executor.execute_action(
        "case-2",
        {
            "action_id": "plan_bad",
            "action_type": "prompt_patch",
            "target_path": "../src/openakita/core/agent.py",
            "title": "越界写入",
            "description": "不允许",
        },
        dry_run=True,
    )

    action_record = store.get_action_record("plan_bad")

    assert result.status == "rejected"
    assert action_record is not None
    assert action_record.summary["reason"] in {"target_not_allowed", "invalid_target_path"}


def test_run_learning_executor_applies_whitelisted_actions_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:exec-1",
        severity="high",
        domain="task",
        problem_summary="工具限制导致失败",
        outcome_summary="需要补充工具提示",
        candidate_actions=[
            {
                "action_id": "plan_exec_1",
                "action_type": "tool_hint_patch",
                "target_path": "prompt/overrides/tool-hints.md",
                "title": "补充工具提示",
                "description": "加入工具限制警告",
                "rationale": ["missing_tool"],
                "constraints": ["仅白名单路径"],
                "details": {"suggested_patch_type": "tool_warning"},
            }
        ],
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-1"], note="written", written=True)
    store.replace_candidate_actions(case_id, case.candidate_actions)

    summary = run_learning_executor(limit=10, dry_run=False, store=store)
    action_record = store.get_action_record("plan_exec_1")
    target_file = tmp_path / "prompt" / "overrides" / "tool-hints.md"

    assert summary == {"executed_cases": 1, "action_records": 1}
    assert action_record is not None
    assert action_record.status == "applied"
    assert target_file.exists()
    assert "补充工具提示" in target_file.read_text(encoding="utf-8")


def test_executor_allows_apply_after_dry_run_for_same_action_id(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)
    action = {
        "action_id": "plan_promote_1",
        "action_type": "prompt_patch",
        "target_path": "prompt/overrides/learning-loop.md",
        "title": "降低循环",
        "description": "加入循环退出提示",
        "rationale": ["trace 出现循环"],
        "constraints": ["仅白名单路径"],
        "details": {"suggested_patch_type": "prompt_guardrail"},
    }

    dry_run_result = executor.execute_action("case-3", action, dry_run=True)
    apply_result = executor.execute_action("case-3", action, dry_run=False)
    action_record = store.get_action_record("plan_promote_1")

    assert dry_run_result.status == "dry_run"
    assert apply_result.status == "applied"
    assert action_record is not None
    assert action_record.status == "applied"
    assert (tmp_path / "prompt" / "overrides" / "learning-loop.md").exists()
