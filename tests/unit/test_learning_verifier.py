from __future__ import annotations

from pathlib import Path

from openakita.learning.executor import MinimalLearningExecutor
from openakita.learning.store import LearningStore
from openakita.learning.verifier import MinimalLearningVerifier, run_learning_verifier


def test_minimal_learning_verifier_marks_applied_action_as_verified(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)
    verifier = MinimalLearningVerifier(store=store)
    action = {
        "action_id": "plan_verify_ok",
        "action_type": "prompt_patch",
        "target_path": "prompt/overrides/learning-loop.md",
        "title": "降低循环",
        "description": "加入循环退出提示",
        "rationale": ["trace 出现循环"],
        "constraints": ["仅白名单路径"],
        "details": {"suggested_patch_type": "prompt_guardrail"},
    }

    executor.execute_action("case-v1", action, dry_run=False)
    result = verifier.verify_action("plan_verify_ok", verification_types=["smoke", "replay"])
    action_record = store.get_action_record("plan_verify_ok")

    assert result.status == "verified"
    assert result.rollback_performed is False
    assert action_record is not None
    assert action_record.status == "verified"
    assert action_record.summary["verification_passed"] is True


def test_minimal_learning_verifier_rolls_back_new_file_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)
    verifier = MinimalLearningVerifier(store=store)
    action = {
        "action_id": "plan_verify_fail_new",
        "action_type": "tool_hint_patch",
        "target_path": "prompt/overrides/tool-hints.md",
        "title": "补充工具提示",
        "description": "加入工具限制警告",
        "rationale": ["missing_tool"],
        "constraints": ["仅白名单路径"],
        "details": {"suggested_patch_type": "tool_warning"},
    }

    executor.execute_action("case-v2", action, dry_run=False)
    target_file = tmp_path / "prompt" / "overrides" / "tool-hints.md"
    target_file.write_text("tampered", encoding="utf-8")

    result = verifier.verify_action("plan_verify_fail_new", verification_types=["replay"])
    action_record = store.get_action_record("plan_verify_fail_new")

    assert result.status == "rolled_back"
    assert result.rollback_performed is True
    assert action_record is not None
    assert action_record.status == "rolled_back"
    assert action_record.summary["rollback_reason"] == "new_file_removed"
    assert not target_file.exists()


def test_minimal_learning_verifier_restores_previous_content_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    target_file = tmp_path / "prompt" / "overrides" / "learning-loop.md"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("original content", encoding="utf-8")
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)
    verifier = MinimalLearningVerifier(store=store)
    action = {
        "action_id": "plan_verify_restore",
        "action_type": "prompt_patch",
        "target_path": "prompt/overrides/learning-loop.md",
        "title": "降低循环",
        "description": "加入循环退出提示",
        "rationale": ["trace 出现循环"],
        "constraints": ["仅白名单路径"],
        "details": {"suggested_patch_type": "prompt_guardrail"},
    }

    executor.execute_action("case-v3", action, dry_run=False)
    target_file.write_text("tampered", encoding="utf-8")

    result = verifier.verify_action("plan_verify_restore", verification_types=["replay"])
    action_record = store.get_action_record("plan_verify_restore")

    assert result.status == "rolled_back"
    assert result.rollback_performed is True
    assert action_record is not None
    assert action_record.summary["rollback_reason"] == "content_restored"
    assert target_file.read_text(encoding="utf-8") == "original content"


def test_run_learning_verifier_summarizes_verified_and_rolled_back_actions(
    tmp_path: Path, monkeypatch
) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "learning_enable_low_risk_autofix", True)
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    executor = MinimalLearningExecutor(store=store)
    ok_action = {
        "action_id": "plan_batch_ok",
        "action_type": "prompt_patch",
        "target_path": "prompt/overrides/learning-loop.md",
        "title": "降低循环",
        "description": "加入循环退出提示",
        "rationale": ["trace 出现循环"],
        "constraints": ["仅白名单路径"],
        "details": {"suggested_patch_type": "prompt_guardrail"},
    }
    bad_action = {
        "action_id": "plan_batch_bad",
        "action_type": "skill_backlog_create",
        "target_path": "skills/backlog/",
        "title": "登记技能待办",
        "description": "补充网页抓取技能",
        "rationale": ["能力缺口"],
        "constraints": ["仅白名单路径"],
        "details": {"suggested_patch_type": "skill_backlog"},
    }

    executor.execute_action("case-v4", ok_action, dry_run=False)
    executor.execute_action("case-v5", bad_action, dry_run=False)
    bad_file = tmp_path / "skills" / "backlog" / "plan_batch_bad.md"
    bad_file.write_text("tampered", encoding="utf-8")

    summary = run_learning_verifier(limit=10, verification_types=["replay"], store=store)
    latest_run = store.get_latest_run("system:learning_verifier")

    assert summary["verified_actions"] == 1
    assert summary["rolled_back_actions"] == 1
    assert summary["attempted_actions"] == 2
    assert summary["verification_types"] == ["replay"]
    assert summary["limit"] == 10
    assert latest_run is not None
    assert latest_run.summary == summary
