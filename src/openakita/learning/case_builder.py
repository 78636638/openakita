from __future__ import annotations

import hashlib
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

    def from_orchestration_record(
        self,
        record: dict[str, Any],
        *,
        session_id: str = "",
        conversation_id: str = "",
        user_id: str = "",
        workspace_id: str = "",
    ) -> LearningCase | None:
        status = str(record.get("status", "") or "").strip().lower()
        verification_summary = dict(record.get("verification_summary", {}) or {})
        verification_status = str(verification_summary.get("status", "") or "").strip().lower()
        review_summary = dict(verification_summary.get("review_summary", {}) or {})
        planning_learning_usage = dict(record.get("planning_learning_usage", {}) or {})
        used_learning_actions = [
            item
            for item in (planning_learning_usage.get("used_actions", []) or [])
            if isinstance(item, dict) and str(item.get("description", "") or "").strip()
        ]
        is_successful_learning_case = (
            status == "completed"
            and verification_status == "completed"
            and bool(used_learning_actions)
        )
        review_failed_with_follow_up = (
            status == "completed"
            and verification_status == "incomplete"
            and (
                verification_summary.get("review_passed") is False
                or bool(review_summary.get("blockers"))
                or bool(review_summary.get("missing_deliverables"))
                or verification_summary.get("delivery_verified") is False
                or bool((record.get("next_round_todo") or {}).get("steps"))
                or bool((verification_summary.get("suggested_next_round_todo") or {}).get("steps"))
            )
        )
        if (
            status not in {"failed", "partial_failed"}
            and not is_successful_learning_case
            and not review_failed_with_follow_up
        ):
            return None

        task_message = str(record.get("task_message", "") or "").strip()
        total_steps = int(record.get("total_steps", 0) or 0)
        completed_steps = int(record.get("completed_steps", 0) or 0)
        failed_steps = int(record.get("failed_steps", 0) or 0)
        pending_steps = int(record.get("pending_steps", 0) or 0)
        created_at = (
            str(record.get("completed_at", "") or "").strip()
            or str(record.get("started_at", "") or "").strip()
            or datetime.now().isoformat()
        )
        orchestration_id = self._orchestration_id_for_record(record, session_id=session_id)
        steps = [step for step in (record.get("steps", []) or []) if isinstance(step, dict)]
        next_round_todo = dict(
            record.get("next_round_todo")
            or verification_summary.get("suggested_next_round_todo")
            or {}
        )
        tool_metadata_summary = dict(verification_summary.get("tool_metadata_summary", {}) or {})
        planning_feedback_summary = dict(record.get("planning_feedback_summary", {}) or {})
        failed_step_summaries = self._collect_failed_step_summaries(steps)
        outcome_bits = [
            f"任务: {task_message[:200]}" if task_message else "",
            (
                f"状态={status}，完成 {completed_steps}/{total_steps}，"
                f"失败 {failed_steps}，待处理 {pending_steps}"
            ),
        ]
        verification_reason = str(verification_summary.get("reason", "") or "").strip()
        if verification_reason:
            outcome_bits.append("验收: " + verification_reason[:120])
        if failed_step_summaries:
            outcome_bits.append("失败步骤: " + " ; ".join(failed_step_summaries[:3]))
        if is_successful_learning_case:
            outcome_bits.append(
                "成功补救: "
                + " ; ".join(
                    str(item.get("description", "") or "").strip()
                    for item in used_learning_actions[:3]
                    if str(item.get("description", "") or "").strip()
                )[:180]
            )
        if isinstance(next_round_todo, dict) and next_round_todo.get("steps"):
            outcome_bits.append(
                "下一轮Todo: "
                + " ; ".join(
                    str(step.get("description", "") or "").strip()
                    for step in (next_round_todo.get("steps", []) or [])[:2]
                    if isinstance(step, dict)
                )[:160]
            )
        if isinstance(tool_metadata_summary, dict) and tool_metadata_summary:
            state_counts = tool_metadata_summary.get("delivery_state_counts", {}) or {}
            outcome_bits.append(
                "工具证据: "
                f"tool_results={int(tool_metadata_summary.get('tool_result_count', 0) or 0)}, "
                f"errors={int(tool_metadata_summary.get('error_tool_count', 0) or 0)}, "
                f"receipts={int(tool_metadata_summary.get('delivery_receipt_count', 0) or 0)}, "
                f"delivery={state_counts}"
            )

        tags = ["orchestration_result", "memory_feedback_only", status]
        if self._looks_like_external_delivery_task(task_message):
            tags.append("external_delivery")
        if is_successful_learning_case:
            tags.extend(["successful_remediation", "candidate_action_reuse"])
        review_summary = verification_summary.get("review_summary", {}) or {}
        if isinstance(review_summary, dict):
            if review_summary.get("hallucination_found") is True:
                tags.append("hallucination_completion")
            if verification_summary.get("code_task_detected") and not verification_summary.get(
                "has_test_evidence"
            ):
                tags.append("missing_test_evidence")
            if verification_summary.get("delivery_verified") is False:
                tags.append("delivery_not_verified")
            if review_summary.get("missing_deliverables") or verification_summary.get(
                "missing_deliverables"
            ):
                tags.append("deliverable_mismatch")
            blockers = review_summary.get("blockers") or []
            if any("幻觉" in str(item) for item in blockers):
                tags.append("hallucination_completion")
            if any("测试" in str(item) for item in blockers):
                tags.append("missing_test_evidence")
            if any("交付" in str(item) for item in blockers):
                tags.append("deliverable_mismatch")
        if isinstance(next_round_todo, dict) and next_round_todo.get("steps"):
            tags.append("has_next_round_todo")
        if isinstance(tool_metadata_summary, dict) and tool_metadata_summary:
            tags.append("tool_metadata_learning")
            if tool_metadata_summary.get("has_delivery_metadata"):
                tags.append("delivery_tool_metadata")
            if int(tool_metadata_summary.get("error_tool_count", 0) or 0) > 0:
                tags.append("tool_error_evidence")
            state_counts = tool_metadata_summary.get("delivery_state_counts", {}) or {}
            if int(state_counts.get("failed", 0) or 0) > 0:
                tags.append("delivery_receipt_failed")
            if int(state_counts.get("local_only", 0) or 0) > 0:
                tags.append("delivery_receipt_local_only")

        evidence = []
        work_summary = str(record.get("work_summary", "") or "").strip()
        if work_summary:
            evidence.append(work_summary[:500])
        for item in failed_step_summaries[:3]:
            evidence.append(item[:300])
        if isinstance(next_round_todo, dict) and next_round_todo.get("steps"):
            remediation_lines = [
                str(step.get("description", "") or "").strip()
                for step in (next_round_todo.get("steps", []) or [])[:3]
                if isinstance(step, dict) and str(step.get("description", "") or "").strip()
            ]
            if remediation_lines:
                evidence.append("下一轮 Todo: " + " ; ".join(remediation_lines)[:500])
        if isinstance(tool_metadata_summary, dict) and tool_metadata_summary:
            evidence.append(
                "工具结果元数据: "
                + str(
                    {
                        "tool_names": tool_metadata_summary.get("tool_names", []),
                        "error_tool_count": int(tool_metadata_summary.get("error_tool_count", 0) or 0),
                        "delivery_receipt_count": int(
                            tool_metadata_summary.get("delivery_receipt_count", 0) or 0
                        ),
                        "delivery_state_counts": tool_metadata_summary.get(
                            "delivery_state_counts", {}
                        )
                        or {},
                    }
                )[:500]
            )
        if used_learning_actions:
            evidence.append(
                "复用补救动作: "
                + " ; ".join(
                    str(item.get("description", "") or "").strip()
                    for item in used_learning_actions[:3]
                    if str(item.get("description", "") or "").strip()
                )[:500]
            )

        candidate_actions: list[dict[str, Any]] = []
        if isinstance(next_round_todo, dict) and next_round_todo.get("steps"):
            for index, step in enumerate((next_round_todo.get("steps", []) or [])[:8], start=1):
                if not isinstance(step, dict):
                    continue
                description = str(step.get("description", "") or "").strip()
                if not description:
                    continue
                candidate_actions.append(
                    {
                        "action_type": "next_round_todo_step",
                        "step_id": str(step.get("id", f"step_{index}") or f"step_{index}"),
                        "description": description[:500],
                        "depends_on": list(step.get("depends_on") or []),
                        "source": "review_failure",
                    }
                )
        if used_learning_actions:
            for index, action in enumerate(used_learning_actions[:8], start=1):
                description = str(action.get("description", "") or "").strip()
                if not description:
                    continue
                candidate_actions.append(
                    {
                        "action_type": "successful_candidate_action",
                        "step_id": str(action.get("step_id", f"used_{index}") or f"used_{index}"),
                        "description": description[:500],
                        "depends_on": [],
                        "source": str(action.get("usage_mode", "") or "successful_reuse"),
                        "target_id": str(action.get("target_id", "") or ""),
                    }
                )

        return LearningCase(
            source="orchestration_result",
            source_ref=f"orchestration_result:{session_id or 'unknown'}:{orchestration_id}",
            case_type=(
                "success"
                if is_successful_learning_case
                else (
                    "near_miss"
                    if status == "partial_failed" or review_failed_with_follow_up
                    else "failure"
                )
            ),
            severity=self._infer_orchestration_severity(record),
            domain="orchestration",
            session_id=session_id or None,
            conversation_id=conversation_id or None,
            workspace_id=workspace_id or None,
            user_id=user_id or None,
            created_at=created_at,
            problem_summary=(
                "外部执行编排任务审查未通过"
                if review_failed_with_follow_up
                else self._orchestration_problem_summary(status)
            ),
            outcome_summary="；".join(bit for bit in outcome_bits if bit)[:300],
            evidence=evidence,
            metrics={
                "total_steps": total_steps,
                "completed_steps": completed_steps,
                "failed_steps": failed_steps,
                "pending_steps": pending_steps,
                "verification_status": str(verification_summary.get("status", "") or ""),
                "next_round_step_count": len(next_round_todo.get("steps", []) or [])
                if isinstance(next_round_todo, dict)
                else 0,
                "tool_result_count": int(tool_metadata_summary.get("tool_result_count", 0) or 0),
                "tool_error_count": int(tool_metadata_summary.get("error_tool_count", 0) or 0),
                "tool_delivery_receipt_count": int(
                    tool_metadata_summary.get("delivery_receipt_count", 0) or 0
                ),
                "used_candidate_action_count": len(used_learning_actions),
            },
            tags=list(dict.fromkeys(tags)),
            candidate_actions=candidate_actions,
            lineage={
                "orchestration_id": orchestration_id,
                "task_message": task_message[:500],
                "started_at": record.get("started_at", ""),
                "completed_at": record.get("completed_at", ""),
                "verification_summary": verification_summary,
                "next_round_todo": next_round_todo,
                "planning_learning_usage": planning_learning_usage,
                "planning_feedback_summary": planning_feedback_summary,
                "tool_metadata_summary": tool_metadata_summary,
                "steps": steps[:10],
            },
        )

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

    @staticmethod
    def _infer_orchestration_severity(record: dict[str, Any]) -> str:
        status = str(record.get("status", "") or "").lower()
        total_steps = int(record.get("total_steps", 0) or 0)
        failed_steps = int(record.get("failed_steps", 0) or 0)
        if status == "completed" and int(
            ((record.get("planning_learning_usage", {}) or {}).get("used_action_count", 0) or 0)
        ) > 0:
            return "medium"
        if status == "failed" and total_steps >= 3 and failed_steps >= 2:
            return "high"
        return "medium"

    @staticmethod
    def _orchestration_problem_summary(status: str) -> str:
        if status == "completed":
            return "外部执行编排任务成功复用了历史补救动作"
        if status == "partial_failed":
            return "外部执行编排任务部分失败"
        return "外部执行编排任务失败"

    @staticmethod
    def _looks_like_external_delivery_task(text: str) -> bool:
        normalized = (text or "").lower()
        if not normalized:
            return False
        channel_markers = (
            "飞书", "feishu", "telegram", "微信", "wecom", "钉钉", "dingtalk", "qq",
            "群", "私聊", "消息",
        )
        delivery_markers = (
            "推送", "发送", "发给", "发到", "交付", "上传", "回复",
            "二维码", "附件", "图片", "文件", "截图",
        )
        return any(marker in normalized for marker in channel_markers) and any(
            marker in normalized for marker in delivery_markers
        )

    @staticmethod
    def _collect_failed_step_summaries(steps: list[dict[str, Any]]) -> list[str]:
        summaries: list[str] = []
        for step in steps:
            if str(step.get("status", "") or "").lower() not in {"failed", "pending"}:
                continue
            desc = str(step.get("description", "") or "").strip()
            preview = str(step.get("result_preview", "") or "").strip()
            if preview and preview != desc:
                summaries.append(f"{desc}: {preview}"[:300])
            elif desc:
                summaries.append(desc[:300])
        return summaries

    @staticmethod
    def _orchestration_id_for_record(record: dict[str, Any], *, session_id: str) -> str:
        existing = str(record.get("orchestration_id", "") or "").strip()
        if existing:
            return existing
        stable_key = "|".join(
            [
                session_id,
                str(record.get("completed_at", "") or ""),
                str(record.get("started_at", "") or ""),
                str(record.get("task_message", "") or ""),
            ]
        )
        return hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]
