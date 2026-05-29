from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from .executor import MinimalLearningExecutor
from .planner import run_learning_planner
from .store import LearningStore


def run_learning_shadow(
    *,
    limit: int = 100,
    store: LearningStore | None = None,
) -> dict[str, Any]:
    """Run the minimum shadow loop: planner -> executor(dry_run) -> run summary."""

    current_store = store or LearningStore()
    started_at = datetime.now().isoformat()
    planner_summary = run_learning_planner(limit=limit, store=current_store)
    executor = MinimalLearningExecutor(store=current_store)

    candidate_cases = current_store.list_cases_with_candidate_actions(limit=limit)
    pending_cases = 0
    pending_actions = 0
    shadowed_cases = 0
    shadowed_actions = 0
    rejected_actions = 0
    skipped_existing_records = 0
    preview_targets: list[str] = []

    for case in candidate_cases:
        case_pending = 0
        case_shadowed = 0
        for action in case.candidate_actions:
            action_id = str(action.get("action_id", "")).strip()
            if action_id and current_store.has_action_record(action_id):
                skipped_existing_records += 1
                continue

            case_pending += 1
            pending_actions += 1
            result = executor.execute_action(case.case_id, action, dry_run=True)
            if result.status == "dry_run":
                case_shadowed += 1
                shadowed_actions += 1
                resolved_target = str(result.summary.get("resolved_target", "")).strip() if result.summary else ""
                if resolved_target:
                    preview_targets.append(resolved_target)
            elif result.status == "rejected":
                rejected_actions += 1

        if case_pending:
            pending_cases += 1
        if case_shadowed:
            shadowed_cases += 1

    summary = {
        "status": "completed",
        "mode": "shadow",
        "planned_cases": planner_summary["planned_cases"],
        "generated_actions": planner_summary["generated_actions"],
        "candidate_cases_total": len(candidate_cases),
        "pending_cases": pending_cases,
        "pending_actions": pending_actions,
        "shadowed_cases": shadowed_cases,
        "shadowed_actions": shadowed_actions,
        "rejected_actions": rejected_actions,
        "skipped_existing_records": skipped_existing_records,
        "preview_targets": list(dict.fromkeys(preview_targets))[:10],
    }

    finished_at = datetime.now().isoformat()
    current_store.record_run(
        run_id=uuid4().hex[:12],
        trigger_source="system:learning_shadow",
        status="ok",
        summary=summary,
        started_at=started_at,
        finished_at=finished_at,
    )
    return summary
