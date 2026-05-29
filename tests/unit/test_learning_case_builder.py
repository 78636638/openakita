from __future__ import annotations

from datetime import datetime

from openakita.evaluation.metrics import EvalMetrics, EvalResult, TraceMetrics
from openakita.evolution.failure_analysis import (
    FailureAnalysisResult,
    FailureMetrics,
    HarnessGap,
    RootCause,
)
from openakita.evolution.self_check import CheckReport
from openakita.evolution.self_check import TestResult as SelfCheckTestResult
from openakita.learning.case_builder import LearningCaseBuilder


def test_case_builder_builds_selfcheck_case() -> None:
    report = CheckReport(
        timestamp=datetime(2026, 1, 1, 8, 0, 0),
        total_tests=3,
        passed=2,
        failed=1,
        results=[
            SelfCheckTestResult(
                test_id="core_memory_001",
                passed=False,
                error="memory db corrupted",
                duration_ms=12,
            )
        ],
    )

    cases = LearningCaseBuilder().from_check_report(report)

    assert len(cases) == 1
    case = cases[0]
    assert case.source == "self_check"
    assert case.case_type == "failure"
    assert case.severity == "high"
    assert case.domain == "core"
    assert case.problem_summary == "自检失败: core_memory_001"
    assert "anti_pattern" in case.tags


def test_case_builder_builds_failure_analysis_case() -> None:
    result = FailureAnalysisResult(
        task_id="task-123",
        timestamp="2026-01-02T09:00:00",
        root_cause=RootCause.TOOL_LIMITATION,
        harness_gap=HarnessGap.MISSING_TOOL,
        metrics=FailureMetrics(total_tokens=60000, total_iterations=12, error_count=2),
        evidence=["Exit reason: max_iterations"],
        suggestion="need better tool",
        raw_data={"task_description": "抓取并整理网页信息"},
    )

    case = LearningCaseBuilder().from_failure_analysis(result)

    assert case.source == "failure_analysis"
    assert case.task_id == "task-123"
    assert case.root_cause == "tool_limitation"
    assert case.harness_gap == "missing_tool"
    assert case.severity == "high"
    assert case.problem_summary == "抓取并整理网页信息"


def test_case_builder_builds_daily_evaluation_cases() -> None:
    result = EvalResult(
        trace_id="trace-001",
        metrics=TraceMetrics(
            trace_id="trace-001",
            session_id="session-001",
            timestamp=datetime(2026, 1, 3, 10, 0, 0).timestamp(),
            total_iterations=18,
            total_tool_calls=6,
            total_input_tokens=1200,
            total_output_tokens=800,
            total_duration_ms=42000,
            task_completed=False,
            tool_errors=2,
            loop_detected=True,
        ),
        judge_score=0.35,
        judge_reasoning="trace 出现循环且未完成任务",
        judge_suggestions=["增强循环检测提示"],
        tags=["failed", "loop"],
    )
    metrics = EvalMetrics(
        total_traces=3,
        period_start=datetime(2026, 1, 3, 0, 0, 0).timestamp(),
        period_end=datetime(2026, 1, 3, 23, 59, 0).timestamp(),
        task_completion_rate=0.33,
        tool_selection_accuracy=0.5,
        avg_tool_calls_per_task=5.0,
        avg_iterations=16.0,
        avg_token_usage=6000,
        avg_latency_ms=25000,
        loop_detection_rate=0.34,
        error_recovery_rate=0.2,
        rollback_rate=0.15,
        avg_judge_score=0.42,
    )

    cases = LearningCaseBuilder().from_daily_evaluation(
        metrics,
        [result],
        report_path="data/evaluation/eval_20260103_100000.json",
        action_types=["memory", "prompt"],
    )

    assert len(cases) == 2
    summary_case = cases[0]
    trace_case = cases[1]
    assert summary_case.source == "daily_evaluation"
    assert summary_case.case_type == "failure"
    assert summary_case.domain == "evaluation"
    assert summary_case.severity == "high"
    assert "summary" in summary_case.tags
    assert "memory" in summary_case.tags
    assert trace_case.source == "daily_evaluation"
    assert trace_case.trace_id == "trace-001"
    assert trace_case.case_type == "failure"
    assert trace_case.severity == "high"
    assert "failed" in trace_case.tags
    assert "loop" in trace_case.tags
