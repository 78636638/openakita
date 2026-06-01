"""Lightweight read-only endpoints for learning case inspection."""

from __future__ import annotations

from fastapi import APIRouter, Query

from openakita.learning.store import LearningStore

router = APIRouter()


@router.get("/api/learning/orchestration-cases")
async def list_orchestration_cases(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Return recent orchestration-result learning cases for inspection."""
    store = LearningStore()
    cases = store.list_cases(limit=limit, source="orchestration_result")
    return {
        "cases": [_serialize_case(case) for case in cases],
        "returned": len(cases),
        "source": "orchestration_result",
        "limit": limit,
    }


def _serialize_case(case) -> dict:
    lineage = dict(getattr(case, "lineage", {}) or {})
    return {
        "case_id": case.case_id,
        "source": case.source,
        "source_ref": case.source_ref,
        "case_type": case.case_type,
        "severity": case.severity,
        "domain": case.domain,
        "session_id": case.session_id,
        "conversation_id": case.conversation_id,
        "created_at": case.created_at,
        "problem_summary": case.problem_summary,
        "outcome_summary": case.outcome_summary,
        "tags": list(case.tags),
        "metrics": dict(case.metrics),
        "evidence": list(case.evidence),
        "verification_summary": dict(lineage.get("verification_summary", {}) or {}),
    }


__all__ = ["router"]
