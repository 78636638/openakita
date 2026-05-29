from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from openakita.evaluation.metrics import EvalMetrics, EvalResult, TraceMetrics
from openakita.learning.models import LearningCase
from openakita.learning.scheduler_hooks import run_daily_evaluation
from openakita.learning.store import LearningStore


class _FakeDailyEvaluator:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def collect_daily_eval(self):
        result = EvalResult(
            trace_id="trace-eval-001",
            metrics=TraceMetrics(
                trace_id="trace-eval-001",
                session_id="session-eval-001",
                timestamp=datetime(2026, 1, 4, 8, 0, 0).timestamp(),
                total_iterations=20,
                total_tool_calls=7,
                total_input_tokens=1400,
                total_output_tokens=900,
                total_duration_ms=58000,
                task_completed=False,
                tool_errors=2,
                loop_detected=True,
            ),
            judge_score=0.3,
            judge_reasoning="评估发现任务未完成且存在循环",
            judge_suggestions=["补充循环退出策略"],
            tags=["failed", "loop"],
        )
        metrics = EvalMetrics(
            total_traces=2,
            period_start=datetime(2026, 1, 4, 0, 0, 0).timestamp(),
            period_end=datetime(2026, 1, 4, 8, 30, 0).timestamp(),
            task_completion_rate=0.5,
            tool_selection_accuracy=0.5,
            avg_tool_calls_per_task=6.0,
            avg_iterations=18.0,
            avg_token_usage=5000,
            avg_latency_ms=32000,
            loop_detection_rate=0.5,
            error_recovery_rate=0.0,
            rollback_rate=0.0,
            avg_judge_score=0.45,
        )
        actions = [type("Action", (), {"action_type": "memory"})()]
        return metrics, [result], actions, "data/evaluation/eval_fake.json"


@pytest.mark.asyncio
async def test_run_daily_evaluation_writes_learning_cases(tmp_path: Path, monkeypatch) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(
        "openakita.evaluation.optimizer.DailyEvaluator",
        _FakeDailyEvaluator,
    )
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    case_id, _ = store.upsert_case(
        store.get_case_by_source_ref("self_check:credit:seed")
        or LearningCase(
            source="self_check",
            source_ref="self_check:credit:seed",
            problem_summary="credit seed",
            outcome_summary="seed",
        )
    )
    store.mark_reviewed(case_id, memory_ids=["mem-credit-001"], note="seed", written=True)
    store.record_memory_hits(["mem-credit-001"], query="credit query", source="search_memory")

    summary = await run_daily_evaluation(brain=object())
    store = LearningStore(db_path=tmp_path / "data" / "learning" / "openakita_learning.db")
    cases = store.list_cases(limit=10)
    latest_run = store.get_latest_run("system:daily_evaluation")
    memory_credit = store.get_credit_stat("memory", "mem-credit-001")
    case_credit = store.get_credit_stat("case", case_id)

    assert summary["status"] == "completed"
    assert summary["traces_evaluated"] == 1
    assert summary["generated_cases"] == 2
    assert summary["inserted_cases"] == 2
    assert "hit_stats" in summary
    assert summary["credit_outcome"] == "harmful"
    assert summary["credit_observations"] == 2
    assert "credit_stats" in summary
    assert len(cases) == 3
    assert sum(1 for case in cases if case.source == "daily_evaluation") == 2
    assert latest_run is not None
    assert latest_run.summary["hit_stats"] == summary["hit_stats"]
    assert latest_run.summary["credit_stats"] == summary["credit_stats"]
    assert memory_credit is not None
    assert memory_credit.harmful_count == 1
    assert case_credit is not None
    assert case_credit.harmful_count == 1
