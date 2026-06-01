from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
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
    scanned_failure_files = 0
    inserted_failure_cases = 0
    scanned_orchestration_records = 0
    inserted_orchestration_cases = 0
    base = settings.data_dir / "failure_analysis"
    if not base.exists():
        base_paths = []
    else:
        base_paths = sorted(base.rglob("*.json"))

    for path in base_paths:
        scanned += 1
        scanned_failure_files += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            case = builder.from_failure_analysis_payload(payload)
            _, created = store.upsert_case(case)
            if created:
                inserted += 1
                inserted_failure_cases += 1
        except Exception:
            continue

    for session, record in _iter_persisted_session_orchestration_records():
        scanned += 1
        scanned_orchestration_records += 1
        try:
            case = builder.from_orchestration_record(
                record,
                session_id=session.id,
                conversation_id=session.session_key,
                user_id=session.user_id,
            )
            if not case:
                continue
            _, created = store.upsert_case(case)
            if created:
                inserted += 1
                inserted_orchestration_cases += 1
        except Exception:
            continue

    _record_run(
        store,
        trigger_source="system:hourly_learning_ingest",
        summary={
            "scanned_files": scanned_failure_files,
            "inserted_cases": inserted,
            "scanned_failure_files": scanned_failure_files,
            "inserted_failure_cases": inserted_failure_cases,
            "scanned_orchestration_records": scanned_orchestration_records,
            "inserted_orchestration_cases": inserted_orchestration_cases,
        },
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
    reviewed_orchestration_cases = 0
    written_orchestration_cases = 0
    orchestration_cases_with_hits = 0
    reviewed_cases: list = []

    for case in store.list_pending_review(limit=200):
        reviewed += 1
        reviewed_cases.append(case)
        is_orchestration_case = case.source == "orchestration_result"
        if is_orchestration_case:
            reviewed_orchestration_cases += 1
        repeat_count = store.count_similar_problem(case.problem_summary, domain=case.domain)
        case_hit_count, candidate_action_hit_count, hit_count = _resolve_review_hit_counts(store, case)
        if hit_count > 0:
            cases_with_hits += 1
            total_case_hits += hit_count
            if is_orchestration_case:
                orchestration_cases_with_hits += 1
        memory_ids = writer.write_case(case, repeat_count=repeat_count)
        store.mark_reviewed(
            case.case_id,
            memory_ids=memory_ids,
            note=_build_review_note(
                case=case,
                written=bool(memory_ids),
                repeat_count=repeat_count,
                hit_count=hit_count,
            ),
            written=bool(memory_ids),
        )
        if memory_ids:
            written += 1
            written_case_hits += hit_count
            if is_orchestration_case:
                written_orchestration_cases += 1

    hit_snapshot = store.summarize_hit_stats(top_n=5)
    credit_snapshot = store.summarize_credit_stats(top_n=5)
    latest_ingest_run = store.get_latest_run("system:hourly_learning_ingest")
    latest_eval_run = store.get_latest_run("system:daily_evaluation")
    latest_shadow_run = store.get_latest_run("system:learning_shadow")
    latest_promote_run = store.get_latest_run("system:learning_promote")
    latest_verifier_run = store.get_latest_run("system:learning_verifier")
    latest_orchestration_case = store.get_latest_case(source="orchestration_result")
    recent_orchestration_cases = store.list_cases(limit=5, source="orchestration_result")
    candidate_experience_pool = store.list_candidate_experience_pool(limit=5)
    successful_candidate_action_panel = _build_successful_candidate_action_panel(
        store,
        reviewed_cases=reviewed_cases,
        top_n=5,
    )

    _record_run(
        store,
        trigger_source="system:daily_learning_review",
        summary={
            "reviewed_cases": reviewed,
            "written_cases": written,
            "cases_with_hits": cases_with_hits,
            "total_case_hits": total_case_hits,
            "written_case_hits": written_case_hits,
            "reviewed_orchestration_cases": reviewed_orchestration_cases,
            "written_orchestration_cases": written_orchestration_cases,
            "orchestration_cases_with_hits": orchestration_cases_with_hits,
            "hit_stats": hit_snapshot,
            "credit_stats": credit_snapshot,
            "latest_credit_outcome": (
                latest_eval_run.summary.get("credit_outcome") if latest_eval_run else None
            ),
            "latest_credit_score": (
                latest_eval_run.summary.get("credit_score") if latest_eval_run else None
            ),
            "latest_orchestration_ingest_metrics": _build_orchestration_ingest_metrics(
                latest_ingest_run
            ),
            "latest_orchestration_ingest_run": _build_run_snapshot(latest_ingest_run),
            "latest_orchestration_case": _build_case_snapshot(latest_orchestration_case),
            "recent_orchestration_cases": _build_case_snapshots(recent_orchestration_cases, limit=5),
            "recent_orchestration_case_count": len(recent_orchestration_cases),
            "successful_candidate_action_panel": successful_candidate_action_panel,
            "candidate_experience_pool_count": len(candidate_experience_pool),
            "candidate_experience_pool": _build_case_snapshots(candidate_experience_pool, limit=5),
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


def _iter_persisted_session_orchestration_records():
    from ..sessions.session import Session

    sessions_file = _session_storage_dir() / "sessions.json"
    if not sessions_file.exists():
        return

    try:
        payload = json.loads(sessions_file.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, list):
        return

    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            session = Session.from_dict(item)
        except Exception:
            continue
        records = getattr(session.context, "orchestration_records", None) or []
        for record in records:
            if isinstance(record, dict):
                yield session, record


def _session_storage_dir() -> Path:
    configured = Path(settings.session_storage_path)
    if configured.is_absolute():
        return configured
    return settings.project_root / configured


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


def _build_case_snapshot(case) -> dict | None:
    if not case:
        return None
    lineage = dict(getattr(case, "lineage", {}) or {})
    return {
        "case_id": case.case_id,
        "source": case.source,
        "source_ref": case.source_ref,
        "status": getattr(case, "status", ""),
        "case_type": case.case_type,
        "severity": case.severity,
        "domain": case.domain,
        "created_at": case.created_at,
        "problem_summary": case.problem_summary,
        "outcome_summary": case.outcome_summary,
        "tags": list(case.tags),
        "candidate_action_count": len(getattr(case, "candidate_actions", []) or []),
        "review_note": getattr(case, "review_note", ""),
        "verification_summary": dict(lineage.get("verification_summary", {}) or {}),
    }


def _build_case_snapshots(cases, *, limit: int = 5) -> list[dict]:
    snapshots: list[dict] = []
    for case in list(cases or [])[:limit]:
        snapshot = _build_case_snapshot(case)
        if snapshot:
            snapshots.append(snapshot)
    return snapshots


def _build_orchestration_ingest_metrics(run) -> dict | None:
    if not run:
        return None

    summary = dict(run.summary)
    return {
        "run_id": run.run_id,
        "status": run.status,
        "scanned_orchestration_records": int(summary.get("scanned_orchestration_records", 0) or 0),
        "inserted_orchestration_cases": int(summary.get("inserted_orchestration_cases", 0) or 0),
        "scanned_failure_files": int(summary.get("scanned_failure_files", 0) or 0),
        "inserted_failure_cases": int(summary.get("inserted_failure_cases", 0) or 0),
        "inserted_cases": int(summary.get("inserted_cases", 0) or 0),
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


def _build_successful_candidate_action_panel(
    store: LearningStore,
    *,
    reviewed_cases: list | None = None,
    top_n: int = 5,
) -> dict:
    historical_cases = store.list_reviewed_cases_with_candidate_actions(limit=200)
    current_run_case_ids = {
        str(getattr(case, "case_id", "") or "")
        for case in (reviewed_cases or [])
        if str(getattr(case, "case_id", "") or "")
    }
    hit_stats = {
        stat.target_id: stat for stat in store.list_hit_stats(target_type="candidate_action", limit=500)
    }
    credit_stats = {
        stat.target_id: stat
        for stat in store.list_credit_stats(target_type="candidate_action", limit=500)
    }

    aggregates: dict[str, dict] = {}
    for case in historical_cases:
        if case.case_type != "success":
            continue
        if "successful_remediation" not in (case.tags or []):
            continue

        for action in case.candidate_actions or []:
            if not isinstance(action, dict):
                continue
            if str(action.get("action_type", "") or "").strip() != "successful_candidate_action":
                continue
            target_id = str(action.get("target_id", "") or "").strip()
            if not target_id:
                continue

            entry = aggregates.setdefault(
                target_id,
                {
                    "target_id": target_id,
                    "description": str(action.get("description", "") or "").strip(),
                    "success_case_count": 0,
                    "current_run_success_case_count": 0,
                    "current_run_helpful_count": 0,
                    "recent_samples": [],
                    "_seen_case_ids": set(),
                    "_current_run_seen_case_ids": set(),
                    "_latest_timestamp": "",
                },
            )
            if not entry["description"]:
                entry["description"] = str(action.get("description", "") or "").strip()
            if case.case_id in entry["_seen_case_ids"]:
                continue

            entry["_seen_case_ids"].add(case.case_id)
            entry["success_case_count"] += 1
            if case.case_id in current_run_case_ids and case.case_id not in entry["_current_run_seen_case_ids"]:
                entry["_current_run_seen_case_ids"].add(case.case_id)
                entry["current_run_success_case_count"] += 1
                planning_feedback = dict((getattr(case, "lineage", {}) or {}).get("planning_feedback_summary", {}) or {})
                if str(planning_feedback.get("credit_outcome", "") or "").strip().lower() == "helpful":
                    entry["current_run_helpful_count"] += 1
            sample_timestamp = str(case.reviewed_at or case.created_at or "")
            if sample_timestamp > str(entry["_latest_timestamp"]):
                entry["_latest_timestamp"] = sample_timestamp
            if len(entry["recent_samples"]) < 3:
                entry["recent_samples"].append(
                    {
                        "case_id": case.case_id,
                        "source_ref": case.source_ref,
                        "reviewed_at": case.reviewed_at,
                        "created_at": case.created_at,
                        "outcome_summary": case.outcome_summary,
                    }
                )

    top_actions: list[dict] = []
    total_hits = 0
    total_helpful = 0
    current_run_helpful = 0
    current_run_success_cases = 0
    for target_id, entry in aggregates.items():
        hit = hit_stats.get(target_id)
        credit = credit_stats.get(target_id)
        hit_count = int(hit.hit_count) if hit else 0
        helpful_count = int(credit.helpful_count) if credit else 0
        neutral_count = int(credit.neutral_count) if credit else 0
        harmful_count = int(credit.harmful_count) if credit else 0
        observed_count = helpful_count + neutral_count + harmful_count
        success_rate = round(helpful_count / observed_count, 4) if observed_count else 0.0
        hit_conversion_rate = (
            round(entry["success_case_count"] / hit_count, 4)
            if hit_count > 0
            else 0.0
        )
        total_hits += hit_count
        total_helpful += helpful_count
        current_run_helpful += int(entry["current_run_helpful_count"])
        current_run_success_cases += int(entry["current_run_success_case_count"])
        top_actions.append(
            {
                "target_id": target_id,
                "description": entry["description"],
                "hit_count": hit_count,
                "success_case_count": entry["success_case_count"],
                "helpful_count": helpful_count,
                "current_run_success_case_count": entry["current_run_success_case_count"],
                "current_run_helpful_count": entry["current_run_helpful_count"],
                "neutral_count": neutral_count,
                "harmful_count": harmful_count,
                "observed_outcome_count": observed_count,
                "success_rate": success_rate,
                "hit_conversion_rate": hit_conversion_rate,
                "last_hit_at": hit.last_hit_at if hit else "",
                "last_outcome": credit.last_outcome if credit else "",
                "last_score": float(credit.last_score) if credit else 0.0,
                "recent_samples": list(entry["recent_samples"]),
                "_latest_timestamp": entry["_latest_timestamp"],
            }
        )

    top_actions.sort(
        key=lambda item: (
            -int(item["hit_count"]),
            -float(item["success_rate"]),
            -int(item["success_case_count"]),
            str(item.get("_latest_timestamp", "") or ""),
        ),
        reverse=False,
    )
    for item in top_actions:
        item.pop("_latest_timestamp", None)

    return {
        "action_count": len(aggregates),
        "total_hits": total_hits,
        "total_helpful_outcomes": total_helpful,
        "current_run_success_case_count": current_run_success_cases,
        "current_run_helpful_outcomes": current_run_helpful,
        "top_actions": top_actions[:top_n],
    }


def _resolve_review_hit_counts(store: LearningStore, case) -> tuple[int, int, int]:
    case_hit_count = store.get_hit_count("case", case.case_id)
    target_ids = [
        str(action.get("target_id", "") or "").strip()
        for action in (getattr(case, "candidate_actions", []) or [])
        if isinstance(action, dict) and str(action.get("target_id", "") or "").strip()
    ]
    candidate_action_hit_count = sum(
        store.get_hit_count("candidate_action", target_id)
        for target_id in dict.fromkeys(target_ids)
    )
    effective_hit_count = case_hit_count
    if (
        getattr(case, "case_type", "") == "success"
        and "successful_remediation" in (getattr(case, "tags", []) or [])
        and candidate_action_hit_count > 0
    ):
        effective_hit_count = candidate_action_hit_count
    return case_hit_count, candidate_action_hit_count, effective_hit_count


def _build_review_note(
    *,
    case,
    written: bool,
    repeat_count: int,
    hit_count: int,
) -> str:
    if written:
        base = "memory_written"
    elif case.case_type == "success" and "successful_remediation" in (case.tags or []):
        base = "candidate_experience_pool"
    else:
        base = "threshold_not_met"
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
