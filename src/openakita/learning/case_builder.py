from __future__ import annotations

from datetime import datetime
from typing import Any

from ..evaluation.metrics import EvalMetrics, EvalResult
from ..evolution.failure_analysis import FailureAnalysisResult
from ..evolution.self_check import CheckReport, TestResult
from .models import LearningCase


class LearningCaseBuilder:
    _HIGH_KEYWORDS = ("brain", "agent", "memory", "scheduler", "llm", "database", "db")

    def from_check_report(self, report: CheckReport) -> list[LearningCase]:
        return [self.from_test_result(report, result) for result in report.results if not result.passed]

    def from_test_result(self, report: CheckReport, result: TestResult) -> LearningCase:
        severity = self._infer_check_severity(result)
        domain = self._infer_check_domain(result)
        error_text = str(result.error or "").strip()
        actual_text = str(result.actual or "").strip()
        problem_summary = f"自检失败: {result.test_id}"
        outcome_summary = error_text or actual_text or "测试未通过"
        evidence = [x for x in [error_text, actual_text] if x]
        tags = ["self_check", result.test_id.split("_")[0].lower()]
        if severity == "high":
            tags.append("anti_pattern")
        return LearningCase(
            source="self_check",
            source_ref=f"self_check:{report.timestamp.isoformat()}:{result.test_id}",
            case_type="failure",
            severity=severity,
            domain=domain,
            created_at=report.timestamp.isoformat(),
            problem_summary=problem_summary,
            outcome_summary=outcome_summary,
            evidence=evidence,
            metrics={
                "pass_rate": report.pass_rate,
                "failed": report.failed,
                "duration_ms": result.duration_ms,
            },
            tags=tags,
            lineage={"test_id": result.test_id},
        )

    def from_failure_analysis(self, result: FailureAnalysisResult) -> LearningCase:
        summary = result.raw_data.get("task_description") or f"任务失败: {result.task_id}"
        return LearningCase(
            source="failure_analysis",
            source_ref=f"failure_analysis:{result.task_id}:{result.timestamp}",
            case_type="failure",
            severity=self._infer_failure_severity(result),
            domain="task",
            task_id=result.task_id,
            created_at=result.timestamp,
            problem_summary=summary[:200],
            outcome_summary=result.suggestion[:300] if result.suggestion else result.root_cause.value,
            root_cause=result.root_cause.value,
            harness_gap=result.harness_gap.value,
            evidence=list(result.evidence),
            metrics={
                "total_tokens": result.metrics.total_tokens,
                "total_iterations": result.metrics.total_iterations,
                "total_tool_calls": result.metrics.total_tool_calls,
                "elapsed_seconds": result.metrics.elapsed_seconds,
                "error_count": result.metrics.error_count,
            },
            tags=["failure_analysis", result.root_cause.value, result.harness_gap.value],
            lineage={"raw_data": dict(result.raw_data)},
        )

    def from_failure_analysis_payload(self, payload: dict[str, Any]) -> LearningCase:
        metrics = payload.get("metrics", {}) or {}
        raw_data = payload.get("raw_data", {}) or {}
        return LearningCase(
            source="failure_analysis",
            source_ref=f"failure_analysis:{payload.get('task_id', 'unknown')}:{payload.get('timestamp', '')}",
            case_type="failure",
            severity=self._infer_payload_severity(payload),
            domain="task",
            task_id=payload.get("task_id"),
            created_at=payload.get("timestamp") or "",
            problem_summary=raw_data.get("task_description") or f"任务失败: {payload.get('task_id', 'unknown')}",
            outcome_summary=payload.get("suggestion", "") or payload.get("root_cause", "unknown"),
            root_cause=payload.get("root_cause"),
            harness_gap=payload.get("harness_gap"),
            evidence=list(payload.get("evidence", []) or []),
            metrics={
                "total_tokens": metrics.get("total_tokens", 0),
                "total_iterations": metrics.get("total_iterations", 0),
                "total_tool_calls": metrics.get("total_tool_calls", 0),
                "elapsed_seconds": metrics.get("elapsed_seconds", 0),
                "error_count": metrics.get("error_count", 0),
            },
            tags=[
                "failure_analysis",
                str(payload.get("root_cause", "unknown")),
                str(payload.get("harness_gap", "none")),
            ],
            lineage={"raw_data": dict(raw_data)},
        )

    def from_eval_result(
        self,
        result: EvalResult,
        *,
        report_path: str | None = None,
    ) -> LearningCase | None:
        if result.is_good():
            return None

        severity = self._infer_eval_result_severity(result)
        summary_tags = list(dict.fromkeys(["daily_evaluation", *result.tags]))
        evidence = [result.judge_reasoning.strip()] if result.judge_reasoning.strip() else []
        outcome_summary = (
            result.judge_reasoning.strip()
            or f"Judge 评分偏低: {result.judge_score:.2f}"
            or "评估发现需要关注的问题"
        )
        created_at = datetime.fromtimestamp(result.metrics.timestamp).isoformat()
        return LearningCase(
            source="daily_evaluation",
            source_ref=f"daily_evaluation:{result.trace_id}:{int(result.metrics.timestamp)}",
            case_type="failure",
            severity=severity,
            domain="evaluation",
            trace_id=result.trace_id,
            session_id=result.metrics.session_id or None,
            created_at=created_at,
            problem_summary=f"评估发现异常 Trace: {result.trace_id}",
            outcome_summary=outcome_summary[:300],
            evidence=evidence,
            metrics={
                "judge_score": result.judge_score,
                "total_iterations": result.metrics.total_iterations,
                "total_tool_calls": result.metrics.total_tool_calls,
                "tool_errors": result.metrics.tool_errors,
                "duration_ms": result.metrics.total_duration_ms,
                "total_tokens": result.metrics.total_input_tokens + result.metrics.total_output_tokens,
                "task_completed": result.metrics.task_completed,
                "loop_detected": result.metrics.loop_detected,
            },
            tags=summary_tags,
            lineage={
                "report_path": report_path or "",
                "judge_suggestions": list(result.judge_suggestions),
            },
        )

    def from_eval_summary(
        self,
        metrics: EvalMetrics,
        *,
        report_path: str | None = None,
        action_types: list[str] | None = None,
    ) -> LearningCase | None:
        if metrics.total_traces <= 0:
            return None
        if (
            metrics.task_completion_rate >= 0.8
            and metrics.avg_judge_score >= 0.6
            and metrics.loop_detection_rate <= 0.1
            and metrics.rollback_rate <= 0.1
        ):
            return None

        severity = self._infer_eval_summary_severity(metrics)
        created_at = datetime.fromtimestamp(metrics.period_end or datetime.now().timestamp()).isoformat()
        tag_list = ["daily_evaluation", "summary", *list(action_types or [])]
        return LearningCase(
            source="daily_evaluation",
            source_ref=f"daily_evaluation:summary:{int(metrics.period_end or datetime.now().timestamp())}",
            case_type="failure",
            severity=severity,
            domain="evaluation",
            created_at=created_at,
            problem_summary="每日评估发现整体质量回退",
            outcome_summary=(
                f"完成率={metrics.task_completion_rate:.1%}，"
                f"Judge={metrics.avg_judge_score:.2f}，"
                f"循环率={metrics.loop_detection_rate:.1%}，"
                f"回滚率={metrics.rollback_rate:.1%}"
            ),
            metrics=metrics.to_dict(),
            tags=list(dict.fromkeys(tag_list)),
            lineage={"report_path": report_path or "", "total_traces": metrics.total_traces},
        )

    def from_daily_evaluation(
        self,
        metrics: EvalMetrics,
        results: list[EvalResult],
        *,
        report_path: str | None = None,
        action_types: list[str] | None = None,
    ) -> list[LearningCase]:
        cases: list[LearningCase] = []
        summary_case = self.from_eval_summary(
            metrics,
            report_path=report_path,
            action_types=action_types,
        )
        if summary_case:
            cases.append(summary_case)
        for result in results:
            case = self.from_eval_result(result, report_path=report_path)
            if case:
                cases.append(case)
        return cases

    @classmethod
    def _infer_check_severity(cls, result: TestResult) -> str:
        key = f"{result.test_id} {result.error or ''}".lower()
        if any(word in key for word in cls._HIGH_KEYWORDS):
            return "high"
        if "tool" in key or "channel" in key or "config" in key:
            return "medium"
        return "low"

    @staticmethod
    def _infer_check_domain(result: TestResult) -> str:
        prefix = (result.test_id.split("_")[0] if result.test_id else "general").lower()
        if prefix in {"core", "memory", "scheduler", "llm"}:
            return prefix
        if prefix in {"tools", "tool"}:
            return "tool"
        return "general"

    @staticmethod
    def _infer_failure_severity(result: FailureAnalysisResult) -> str:
        if result.metrics.total_iterations >= 20 or result.metrics.total_tokens >= 50000:
            return "high"
        if result.metrics.total_iterations >= 8 or result.metrics.error_count >= 2:
            return "medium"
        return "low"

    @staticmethod
    def _infer_payload_severity(payload: dict[str, Any]) -> str:
        metrics = payload.get("metrics", {}) or {}
        if metrics.get("total_iterations", 0) >= 20 or metrics.get("total_tokens", 0) >= 50000:
            return "high"
        if metrics.get("total_iterations", 0) >= 8 or metrics.get("error_count", 0) >= 2:
            return "medium"
        return "low"

    @staticmethod
    def _infer_eval_result_severity(result: EvalResult) -> str:
        if not result.metrics.task_completed or result.metrics.loop_detected or result.judge_score < 0.4:
            return "high"
        if result.metrics.tool_errors > 0 or result.judge_score < 0.7 or result.metrics.total_iterations >= 15:
            return "medium"
        return "low"

    @staticmethod
    def _infer_eval_summary_severity(metrics: EvalMetrics) -> str:
        if (
            metrics.task_completion_rate < 0.6
            or metrics.avg_judge_score < 0.4
            or metrics.loop_detection_rate > 0.2
        ):
            return "high"
        return "medium"
