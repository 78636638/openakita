from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


def _new_case_id() -> str:
    return uuid4().hex[:16]


@dataclass(slots=True)
class LearningCase:
    case_id: str = field(default_factory=_new_case_id)
    source: str = ""
    source_ref: str = ""
    case_type: str = "failure"
    severity: str = "medium"
    domain: str = "general"

    session_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None
    task_id: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    problem_summary: str = ""
    outcome_summary: str = ""
    root_cause: str | None = None
    harness_gap: str | None = None

    evidence: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    candidate_actions: list[dict[str, Any]] = field(default_factory=list)
    lineage: dict[str, Any] = field(default_factory=dict)
    status: str = "new"
    review_note: str = ""
    reviewed_at: str | None = None
    memory_ids: list[str] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source": self.source,
            "source_ref": self.source_ref,
            "case_type": self.case_type,
            "severity": self.severity,
            "domain": self.domain,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "problem_summary": self.problem_summary,
            "outcome_summary": self.outcome_summary,
            "root_cause": self.root_cause,
            "harness_gap": self.harness_gap,
            "evidence": list(self.evidence),
            "metrics": dict(self.metrics),
            "tags": list(self.tags),
            "candidate_actions": list(self.candidate_actions),
            "lineage": dict(self.lineage),
            "status": self.status,
            "review_note": self.review_note,
            "reviewed_at": self.reviewed_at,
            "memory_ids": list(self.memory_ids),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> LearningCase:
        return cls(
            case_id=record.get("case_id") or _new_case_id(),
            source=record.get("source", ""),
            source_ref=record.get("source_ref", ""),
            case_type=record.get("case_type", "failure"),
            severity=record.get("severity", "medium"),
            domain=record.get("domain", "general"),
            session_id=record.get("session_id"),
            conversation_id=record.get("conversation_id"),
            trace_id=record.get("trace_id"),
            task_id=record.get("task_id"),
            workspace_id=record.get("workspace_id"),
            user_id=record.get("user_id"),
            created_at=record.get("created_at") or datetime.now().isoformat(),
            problem_summary=record.get("problem_summary", ""),
            outcome_summary=record.get("outcome_summary", ""),
            root_cause=record.get("root_cause"),
            harness_gap=record.get("harness_gap"),
            evidence=list(record.get("evidence", []) or []),
            metrics=dict(record.get("metrics", {}) or {}),
            tags=list(record.get("tags", []) or []),
            candidate_actions=list(record.get("candidate_actions", []) or []),
            lineage=dict(record.get("lineage", {}) or {}),
            status=record.get("status", "new"),
            review_note=record.get("review_note", ""),
            reviewed_at=record.get("reviewed_at"),
            memory_ids=list(record.get("memory_ids", []) or []),
        )
