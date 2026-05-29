from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from .executor import MinimalLearningExecutor
from .store import LearningStore


def run_learning_promote(
    *,
    limit: int = 100,
    store: LearningStore | None = None,
) -> dict[str, Any]:
    """Promote previously dry-run learning actions into apply mode."""

    current_store = store or LearningStore()
    executor = MinimalLearningExecutor(store=current_store)
    started_at = datetime.now().isoformat()

    eligible_actions = 0
    promoted_actions = 0
    promoted_cases: set[str] = set()
    rejected_actions = 0
    skipped_missing_case = 0
    skipped_missing_action = 0
    applied_targets: list[str] = []

    for record in current_store.list_action_records(statuses=["dry_run"], limit=limit):
        eligible_actions += 1
        case = current_store.get_case(record.case_id)
        if not case:
            skipped_missing_case += 1
            continue

        action = next(
            (item for item in case.candidate_actions if str(item.get("action_id", "")).strip() == record.action_id),
            None,
        )
        if not action:
            skipped_missing_action += 1
            continue

        result = executor.execute_action(case.case_id, action, dry_run=False)
        if result.status == "applied":
            promoted_actions += 1
            promoted_cases.add(case.case_id)
            resolved_target = str(result.summary.get("resolved_target", "")).strip() if result.summary else ""
            if resolved_target:
                applied_targets.append(resolved_target)
        elif result.status == "rejected":
            rejected_actions += 1

    summary = {
        "status": "completed",
        "mode": "apply",
        "eligible_actions": eligible_actions,
        "promoted_actions": promoted_actions,
        "promoted_cases": len(promoted_cases),
        "rejected_actions": rejected_actions,
        "skipped_missing_case": skipped_missing_case,
        "skipped_missing_action": skipped_missing_action,
        "applied_targets": list(dict.fromkeys(applied_targets))[:10],
    }

    finished_at = datetime.now().isoformat()
    current_store.record_run(
        run_id=uuid4().hex[:12],
        trigger_source="system:learning_promote",
        status="ok",
        summary=summary,
        started_at=started_at,
        finished_at=finished_at,
    )
    return summary
