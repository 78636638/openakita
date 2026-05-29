from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..utils.atomic_io import safe_write
from .store import LearningActionRecord, LearningCheckpointRecord, LearningStore


@dataclass(slots=True)
class VerificationResult:
    action_id: str
    case_id: str
    status: str
    verification_types: list[str]
    rollback_performed: bool
    summary: dict[str, Any]


class MinimalLearningVerifier:
    """Verify applied low-risk actions and restore from checkpoint on failure."""

    def __init__(self, store: LearningStore | None = None) -> None:
        self.store = store or LearningStore()

    def verify_action(
        self,
        action_id: str,
        *,
        verification_types: list[str] | None = None,
    ) -> VerificationResult:
        record = self.store.get_action_record(action_id)
        if not record:
            return VerificationResult(
                action_id=action_id,
                case_id="",
                status="missing",
                verification_types=verification_types or ["smoke"],
                rollback_performed=False,
                summary={"reason": "action_record_not_found"},
            )

        checks = verification_types or self._infer_checks(record)
        if record.status != "applied":
            return VerificationResult(
                action_id=record.action_id,
                case_id=record.case_id,
                status="skipped",
                verification_types=checks,
                rollback_performed=False,
                summary={"reason": f"status_{record.status}_not_verifiable"},
            )

        checkpoint = self.store.get_checkpoint(record.checkpoint_id or "")
        failures: list[str] = []
        for check in checks:
            error = self._run_check(record, checkpoint, check)
            if error:
                failures.append(f"{check}:{error}")

        if failures:
            rollback_summary = self.restore_from_checkpoint(record)
            summary = {
                **record.summary,
                "verification_types": checks,
                "verification_passed": False,
                "verification_errors": failures,
                "rollback_performed": rollback_summary["restored"],
                "rollback_reason": rollback_summary["reason"],
            }
            self.store.record_action_execution(
                action_id=record.action_id,
                case_id=record.case_id,
                action_type=record.action_type,
                target_path=record.target_path,
                status="rolled_back",
                mode=record.mode,
                summary=summary,
                checkpoint_id=record.checkpoint_id,
            )
            return VerificationResult(
                action_id=record.action_id,
                case_id=record.case_id,
                status="rolled_back",
                verification_types=checks,
                rollback_performed=rollback_summary["restored"],
                summary=summary,
            )

        summary = {
            **record.summary,
            "verification_types": checks,
            "verification_passed": True,
            "verification_errors": [],
            "rollback_performed": False,
        }
        self.store.record_action_execution(
            action_id=record.action_id,
            case_id=record.case_id,
            action_type=record.action_type,
            target_path=record.target_path,
            status="verified",
            mode=record.mode,
            summary=summary,
            checkpoint_id=record.checkpoint_id,
        )
        return VerificationResult(
            action_id=record.action_id,
            case_id=record.case_id,
            status="verified",
            verification_types=checks,
            rollback_performed=False,
            summary=summary,
        )

    def restore_from_checkpoint(self, record: LearningActionRecord) -> dict[str, Any]:
        checkpoint = self.store.get_checkpoint(record.checkpoint_id or "")
        if not checkpoint:
            return {"restored": False, "reason": "checkpoint_not_found"}

        checkpoint_path = Path(checkpoint.backup_path)
        if not checkpoint_path.exists():
            return {"restored": False, "reason": "checkpoint_payload_missing"}

        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        target_path = Path(payload["target_path"])
        existed_before = bool(payload.get("exists_before"))
        previous_content = payload.get("content")
        if existed_before:
            safe_write(target_path, str(previous_content or ""), backup=True)
            return {"restored": True, "reason": "content_restored"}

        if target_path.exists():
            target_path.unlink()
        return {"restored": True, "reason": "new_file_removed"}

    def _run_check(
        self,
        record: LearningActionRecord,
        checkpoint: LearningCheckpointRecord | None,
        check: str,
    ) -> str | None:
        target = Path(record.summary.get("resolved_target") or record.target_path)
        if check == "smoke":
            if not target.exists():
                return "target_missing"
            content = target.read_text(encoding="utf-8")
            if not content.strip():
                return "target_empty"
            return None
        if check == "replay":
            if not target.exists():
                return "target_missing"
            expected = str(record.summary.get("rendered_content", "") or "")
            actual = target.read_text(encoding="utf-8")
            if not expected:
                return "rendered_content_missing"
            if actual != expected:
                return "content_mismatch"
            return None
        return f"unsupported_check:{check}"

    @staticmethod
    def _infer_checks(record: LearningActionRecord) -> list[str]:
        if record.action_type in {"prompt_patch", "tool_hint_patch"}:
            return ["smoke"]
        return ["smoke", "replay"]


def run_learning_verifier(
    *,
    limit: int = 100,
    verification_types: list[str] | None = None,
    store: LearningStore | None = None,
) -> dict[str, int]:
    current_store = store or LearningStore()
    verifier = MinimalLearningVerifier(store=current_store)
    started_at = datetime.now().isoformat()
    verified = 0
    rolled_back = 0

    for record in current_store.list_action_records(statuses=["applied"], limit=limit):
        result = verifier.verify_action(record.action_id, verification_types=verification_types)
        if result.status == "verified":
            verified += 1
        elif result.status == "rolled_back":
            rolled_back += 1

    summary = {
        "verified_actions": verified,
        "rolled_back_actions": rolled_back,
        "attempted_actions": verified + rolled_back,
        "verification_types": list(verification_types or []),
        "limit": limit,
    }
    finished_at = datetime.now().isoformat()
    current_store.record_run(
        run_id=uuid4().hex[:12],
        trigger_source="system:learning_verifier",
        status="ok",
        summary=summary,
        started_at=started_at,
        finished_at=finished_at,
    )
    return summary
