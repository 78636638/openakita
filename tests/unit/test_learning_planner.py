from __future__ import annotations

from pathlib import Path

from openakita.learning.models import LearningCase
from openakita.learning.planner import MinimalLearningPlanner, run_learning_planner
from openakita.learning.store import LearningStore


def test_minimal_learning_planner_generates_expected_candidate_types() -> None:
    planner = MinimalLearningPlanner()

    prompt_case = LearningCase(
        source="daily_evaluation",
        source_ref="daily_evaluation:trace-1",
        severity="high",
        domain="evaluation",
        problem_summary="评估发现 trace 循环",
        outcome_summary="judge 评分偏低",
        metrics={"judge_score": 0.35, "total_iterations": 18, "loop_detected": True},
        tags=["failed", "loop"],
    )
    tool_case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:task-1",
        severity="high",
        domain="task",
        problem_summary="工具选择失败",
        outcome_summary="缺少浏览器工具",
        root_cause="tool_limitation",
        harness_gap="missing_tool",
        metrics={"tool_errors": 2},
        tags=["failure_analysis", "missing_tool"],
    )
    skill_case = LearningCase(
        source="daily_evaluation",
        source_ref="daily_evaluation:trace-2",
        severity="medium",
        domain="evaluation",
        problem_summary="能力缺口导致任务未完成",
        outcome_summary="需要新增网页抓取技能",
        tags=["capability_gap"],
        lineage={"judge_suggestions": ["补充网页抓取技能"]},
        metrics={"judge_score": 0.55},
    )

    prompt_actions = planner.plan_case(prompt_case)
    tool_actions = planner.plan_case(tool_case)
    skill_actions = planner.plan_case(skill_case)

    assert {action["action_type"] for action in prompt_actions} >= {"prompt_patch"}
    assert {action["action_type"] for action in tool_actions} >= {"tool_hint_patch"}
    assert {action["action_type"] for action in skill_actions} >= {"skill_backlog_create"}
    assert all(action["execution_mode"] == "shadow" for action in prompt_actions + tool_actions)


def test_run_learning_planner_updates_candidate_actions_in_store(tmp_path: Path) -> None:
    store = LearningStore(db_path=tmp_path / "learning.db")
    case = LearningCase(
        source="failure_analysis",
        source_ref="failure_analysis:task-plan-1",
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

    summary = run_learning_planner(limit=10, store=store)
    planned_case = store.get_case_by_source_ref("failure_analysis:task-plan-1")

    assert summary == {"planned_cases": 1, "generated_actions": 1}
    assert planned_case is not None
    assert len(planned_case.candidate_actions) == 1
    assert planned_case.candidate_actions[0]["action_type"] == "tool_hint_patch"
