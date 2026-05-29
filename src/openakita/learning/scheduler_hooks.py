from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from ..config import settings
from .case_builder import LearningCaseBuilder
from .feedback_writer import MemoryFeedbackWriter
from .store import LearningStore


def run_learning_ingest() -> tuple[int, int]:
    store = LearningStore()
    builder = LearningCaseBuilder()
    inserted = 0
    scanned = 0
    base = settings.data_dir / "failure_analysis"
    if not base.exists():
        return 0, 0

    for path in sorted(base.rglob("*.json")):
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            case = builder.from_failure_analysis_payload(payload)
            _, created = store.upsert_case(case)
            if created:
                inserted += 1
        except Exception:
            continue

    _record_run(
        store,
        trigger_source="system:hourly_learning_ingest",
        summary={"scanned_files": scanned, "inserted_cases": inserted},
    )
    return scanned, inserted


def run_learning_review(memory_manager) -> tuple[int, int]:
    store = LearningStore()
    writer = MemoryFeedbackWriter(
        memory_manager,
        min_repeat=settings.learning_min_repeat_for_memory_write,
    )
    reviewed = 0
    written = 0
    cases_with_hits = 0
    total_case_hits = 0
    written_case_hits = 0

    for case in store.list_pending_review(limit=200):
        reviewed += 1
        repeat_count = store.count_similar_problem(case.problem_summary, domain=case.domain)
        hit_count = store.get_hit_count("case", case.case_id)
        if hit_count > 0:
            cases_with_hits += 1
            total_case_hits += hit_count
        memory_ids = writer.write_case(case, repeat_count=repeat_count)
        store.mark_reviewed(
            case.case_id,
            memory_ids=memory_ids,
            note=_build_review_note(
                written=bool(memory_ids),
                repeat_count=repeat_count,
                hit_count=hit_count,
            ),
            written=bool(memory_ids),
        )
        if memory_ids:
            written += 1
            written_case_hits += hit_count

    hit_snapshot = store.summarize_hit_stats(top_n=5)
    credit_snapshot = store.summarize_credit_stats(top_n=5)
    latest_eval_run = store.get_latest_run("system:daily_evaluation")
    latest_shadow_run = store.get_latest_run("system:learning_shadow")
    latest_promote_run = store.get_latest_run("system:learning_promote")
    latest_verifier_run = store.get_latest_run("system:learning_verifier")

    _record_run(
        store,
        trigger_source="system:daily_learning_review",
        summary={
            "reviewed_cases": reviewed,
            "written_cases": written,
            "cases_with_hits": cases_with_hits,
            "total_case_hits": total_case_hits,
            "written_case_hits": written_case_hits,
            "hit_stats": hit_snapshot,
            "credit_stats": credit_snapshot,
            "latest_credit_outcome": (
                latest_eval_run.summary.get("credit_outcome") if latest_eval_run else None
            ),
            "latest_credit_score": (
                latest_eval_run.summary.get("credit_score") if latest_eval_run else None
            ),
            "latest_shadow_metrics": _build_shadow_metrics(latest_shadow_run),
            "latest_shadow_run": _build_run_snapshot(latest_shadow_run),
            "latest_promote_metrics": _build_promote_metrics(latest_promote_run),
            "latest_promote_run": _build_run_snapshot(latest_promote_run),
            "latest_verifier_metrics": _build_verifier_metrics(latest_verifier_run),
            "latest_verifier_run": _build_run_snapshot(latest_verifier_run),
        },
    )
    return reviewed, written


async def run_daily_evaluation(brain=None) -> dict[str, object]:
    from ..evaluation.optimizer import DailyEvaluator

    output_dir = settings.project_root / settings.evaluation_output_dir
    evaluator = DailyEvaluator(
        brain=brain,
        traces_dir=str(settings.data_dir / "traces"),
        output_dir=str(output_dir),
        memory_file=str(settings.memory_path),
    )
    metrics, results, actions, report_path = await evaluator.collect_daily_eval()

    store = LearningStore()
    builder = LearningCaseBuilder()
    action_types = [action.action_type for action in actions]
    cases = builder.from_daily_evaluation(
        metrics,
        results,
        report_path=report_path,
        action_types=action_types,
    )
    inserted = 0
    for case in cases:
        _, created = store.upsert_case(case)
        if created:
            inserted += 1
    credit_outcome, credit_score = _derive_credit_observation(metrics)
    credit_observations = store.record_credit_for_hit_stats(
        outcome=credit_outcome,
        source="daily_evaluation",
        score=credit_score,
        context_ref=report_path or "",
        limit=200,
    )
    hit_snapshot = store.summarize_hit_stats(top_n=5)
    credit_snapshot = store.summarize_credit_stats(top_n=5)

    summary = {
        "status": "completed" if results else "no_data",
        "traces_evaluated": len(results),
        "generated_cases": len(cases),
        "inserted_cases": inserted,
        "report_path": report_path or "",
        "action_types": action_types,
        "hit_stats": hit_snapshot,
        "credit_outcome": credit_outcome,
        "credit_score": credit_score,
        "credit_observations": credit_observations,
        "credit_stats": credit_snapshot,
    }
    _record_run(
        store,
        trigger_source="system:daily_evaluation",
        summary=summary,
    )
    return summary


def _record_run(store: LearningStore, *, trigger_source: str, summary: dict) -> None:
    now = datetime.now().isoformat()
    store.record_run(
        run_id=uuid4().hex[:12],
        trigger_source=trigger_source,
        status="ok",
        summary=summary,
        started_at=now,
        finished_at=now,
    )


def _build_run_snapshot(run) -> dict | None:
    if not run:
        return None

    return {
        "run_id": run.run_id,
        "trigger_source": run.trigger_source,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summary": dict(run.summary),
    }


def _build_shadow_metrics(run) -> dict | None:
    if not run:
        return None

    summary = dict(run.summary)
    preview_targets = summary.get("preview_targets", []) or []
    return {
        "run_id": run.run_id,
        "status": run.status,
        "mode": summary.get("mode", ""),
        "planned_cases": int(summary.get("planned_cases", 0) or 0),
        "generated_actions": int(summary.get("generated_actions", 0) or 0),
        "candidate_cases_total": int(summary.get("candidate_cases_total", 0) or 0),
        "pending_cases": int(summary.get("pending_cases", 0) or 0),
        "pending_actions": int(summary.get("pending_actions", 0) or 0),
        "shadowed_cases": int(summary.get("shadowed_cases", 0) or 0),
        "shadowed_actions": int(summary.get("shadowed_actions", 0) or 0),
        "rejected_actions": int(summary.get("rejected_actions", 0) or 0),
        "skipped_existing_records": int(summary.get("skipped_existing_records", 0) or 0),
        "preview_target_count": len(preview_targets),
    }


def _build_verifier_metrics(run) -> dict | None:
    if not run:
        return None

    summary = dict(run.summary)
    verification_types = summary.get("verification_types", []) or []
    return {
        "run_id": run.run_id,
        "status": run.status,
        "verified_actions": int(summary.get("verified_actions", 0) or 0),
        "rolled_back_actions": int(summary.get("rolled_back_actions", 0) or 0),
        "attempted_actions": int(summary.get("attempted_actions", 0) or 0),
        "verification_types": list(verification_types),
        "verification_type_count": len(verification_types),
        "limit": int(summary.get("limit", 0) or 0),
    }


def _build_promote_metrics(run) -> dict | None:
    if not run:
        return None

    summary = dict(run.summary)
    applied_targets = summary.get("applied_targets", []) or []
    return {
        "run_id": run.run_id,
        "status": run.status,
        "mode": summary.get("mode", ""),
        "eligible_actions": int(summary.get("eligible_actions", 0) or 0),
        "promoted_actions": int(summary.get("promoted_actions", 0) or 0),
        "promoted_cases": int(summary.get("promoted_cases", 0) or 0),
        "rejected_actions": int(summary.get("rejected_actions", 0) or 0),
        "skipped_missing_case": int(summary.get("skipped_missing_case", 0) or 0),
        "skipped_missing_action": int(summary.get("skipped_missing_action", 0) or 0),
        "applied_target_count": len(applied_targets),
    }


def _build_review_note(*, written: bool, repeat_count: int, hit_count: int) -> str:
    base = "memory_written" if written else "threshold_not_met"
    return f"{base}; repeat_count={repeat_count}; hit_count={hit_count}"


def _derive_credit_observation(metrics) -> tuple[str, float]:
    score = round(
        (metrics.task_completion_rate * 0.5)
        + (metrics.avg_judge_score * 0.4)
        - (metrics.loop_detection_rate * 0.3)
        - (metrics.rollback_rate * 0.2),
        4,
    )
    if metrics.task_completion_rate >= 0.8 and metrics.avg_judge_score >= 0.7:
        return "helpful", score
    if (
        metrics.task_completion_rate < 0.6
        or metrics.avg_judge_score < 0.5
        or metrics.loop_detection_rate > 0.2
    ):
        return "harmful", score
    return "neutral", score
