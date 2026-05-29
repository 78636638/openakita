from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import settings
from ..utils.atomic_io import safe_json_write, safe_write
from .store import LearningStore

ALLOWED_PREFIXES = ("prompt/overrides/", "skills/backlog/")
ALLOWED_ACTION_TYPES = {"prompt_patch", "tool_hint_patch", "skill_backlog_create"}


def _new_checkpoint_id() -> str:
    return f"ckpt_{uuid4().hex[:12]}"


def render_action_content(action: dict[str, Any]) -> str:
    title = str(action.get("title", "Learning Action")).strip()
    description = str(action.get("description", "")).strip()
    rationale = [str(item).strip() for item in action.get("rationale", []) if str(item).strip()]
    constraints = [str(item).strip() for item in action.get("constraints", []) if str(item).strip()]
    details = action.get("details", {}) or {}
    rationale_lines = [f"- {item}" for item in rationale] or ["- N/A"]
    constraint_lines = [f"- {item}" for item in constraints] or ["- N/A"]
    lines = [
        f"# {title}",
        "",
        description,
        "",
        "## Rationale",
        *rationale_lines,
        "",
        "## Constraints",
        *constraint_lines,
        "",
        "## Details",
        "```json",
        json.dumps(details, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


@dataclass(slots=True)
class ExecutionResult:
    action_id: str
    case_id: str
    action_type: str
    status: str
    mode: str
    target_path: str
    checkpoint_id: str | None = None
    summary: dict[str, Any] | None = None


class MinimalLearningExecutor:
    """Execute planner candidates in a strict whitelist with dry-run by default."""

    def __init__(self, store: LearningStore | None = None) -> None:
        self.store = store or LearningStore()

    def execute_action(self, case_id: str, action: dict[str, Any], *, dry_run: bool = True) -> ExecutionResult:
        action_id = str(action.get("action_id", "")).strip()
        action_type = str(action.get("action_type", "")).strip()
        target_path = str(action.get("target_path", "")).strip()
        mode = "dry_run" if dry_run else "apply"

        if not action_id or action_type not in ALLOWED_ACTION_TYPES:
            return self._record_result(
                action_id=action_id or f"invalid_{uuid4().hex[:8]}",
                case_id=case_id,
                action_type=action_type or "unknown",
                target_path=target_path,
                status="rejected",
                mode=mode,
                summary={"reason": "unsupported_action_type"},
            )

        if self.store.has_action_record(action_id):
            existing = self.store.get_action_record(action_id)
            if dry_run is False and existing.status == "dry_run":
                pass
            else:
                return ExecutionResult(
                    action_id=existing.action_id,
                    case_id=existing.case_id,
                    action_type=existing.action_type,
                    status="skipped",
                    mode=existing.mode,
                    target_path=existing.target_path,
                    checkpoint_id=existing.checkpoint_id,
                    summary={**existing.summary, "reason": "already_recorded"},
                )

        if not self._is_allowed_target(target_path):
            return self._record_result(
                action_id=action_id,
                case_id=case_id,
                action_type=action_type,
                target_path=target_path,
                status="rejected",
                mode=mode,
                summary={"reason": "target_not_allowed"},
            )

        try:
            resolved_target = self._resolve_target_path(action)
        except ValueError as exc:
            return self._record_result(
                action_id=action_id,
                case_id=case_id,
                action_type=action_type,
                target_path=target_path,
                status="rejected",
                mode=mode,
                summary={"reason": "invalid_target_path", "error": str(exc)},
            )
        checkpoint_id = self._create_checkpoint(action_id, resolved_target)

        rendered_content = render_action_content(action)

        if dry_run:
            return self._record_result(
                action_id=action_id,
                case_id=case_id,
                action_type=action_type,
                target_path=str(resolved_target.relative_to(settings.project_root)),
                status="dry_run",
                mode=mode,
                checkpoint_id=checkpoint_id,
                summary={
                    "resolved_target": str(resolved_target),
                    "preview": rendered_content,
                    "rendered_content": rendered_content,
                },
            )

        if not settings.learning_enable_low_risk_autofix:
            return self._record_result(
                action_id=action_id,
                case_id=case_id,
                action_type=action_type,
                target_path=str(resolved_target.relative_to(settings.project_root)),
                status="rejected",
                mode=mode,
                checkpoint_id=checkpoint_id,
                summary={"reason": "autofix_disabled"},
            )

        safe_write(resolved_target, rendered_content, backup=True)
        return self._record_result(
            action_id=action_id,
            case_id=case_id,
            action_type=action_type,
            target_path=str(resolved_target.relative_to(settings.project_root)),
            status="applied",
            mode=mode,
            checkpoint_id=checkpoint_id,
            summary={
                "resolved_target": str(resolved_target),
                "bytes_written": len(rendered_content.encode("utf-8")),
                "rendered_content": rendered_content,
            },
        )

    def execute_case_actions(self, case, *, dry_run: bool = True) -> list[ExecutionResult]:
        return [self.execute_action(case.case_id, action, dry_run=dry_run) for action in case.candidate_actions]

    @staticmethod
    def _is_allowed_target(target_path: str) -> bool:
        normalized = target_path.replace("\\", "/")
        if ".." in Path(normalized).parts:
            return False
        return any(normalized.startswith(prefix) for prefix in ALLOWED_PREFIXES)

    @staticmethod
    def _resolve_target_path(action: dict[str, Any]) -> Path:
        raw_target = str(action.get("target_path", "")).strip().replace("\\", "/")
        if str(action.get("action_type")) == "skill_backlog_create" and raw_target.endswith("/"):
            filename = f"{action.get('action_id', 'candidate')}.md"
            raw_target = raw_target + filename
        resolved = (settings.project_root / raw_target).resolve()
        if not str(resolved).startswith(str(settings.project_root.resolve())):
            raise ValueError("resolved target escaped project root")
        return resolved

    def _create_checkpoint(self, action_id: str, target_path: Path) -> str:
        checkpoint_id = _new_checkpoint_id()
        checkpoint_dir = settings.project_root / "data" / "learning" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        backup_path = checkpoint_dir / f"{checkpoint_id}.json"
        payload = {
            "target_path": str(target_path),
            "exists_before": target_path.exists(),
            "content": target_path.read_text(encoding="utf-8") if target_path.exists() else None,
        }
        safe_json_write(backup_path, payload, indent=2, backup=True)
        created_at = datetime.now().isoformat()
        self.store.record_checkpoint(
            checkpoint_id=checkpoint_id,
            action_id=action_id,
            target_path=str(target_path),
            backup_path=str(backup_path),
            exists_before=bool(payload["exists_before"]),
            created_at=created_at,
        )
        return checkpoint_id

    def _record_result(
        self,
        *,
        action_id: str,
        case_id: str,
        action_type: str,
        target_path: str,
        status: str,
        mode: str,
        summary: dict[str, Any],
        checkpoint_id: str | None = None,
    ) -> ExecutionResult:
        self.store.record_action_execution(
            action_id=action_id,
            case_id=case_id,
            action_type=action_type,
            target_path=target_path,
            status=status,
            mode=mode,
            summary=summary,
            checkpoint_id=checkpoint_id,
        )
        return ExecutionResult(
            action_id=action_id,
            case_id=case_id,
            action_type=action_type,
            status=status,
            mode=mode,
            target_path=target_path,
            checkpoint_id=checkpoint_id,
            summary=summary,
        )


def run_learning_executor(
    *,
    limit: int = 100,
    dry_run: bool = True,
    store: LearningStore | None = None,
) -> dict[str, int]:
    current_store = store or LearningStore()
    executor = MinimalLearningExecutor(store=current_store)
    executed_cases = 0
    action_records = 0

    for case in current_store.list_cases_with_candidate_actions(limit=limit):
        results = executor.execute_case_actions(case, dry_run=dry_run)
        if not results:
            continue
        executed_cases += 1
        action_records += len(results)

    return {
        "executed_cases": executed_cases,
        "action_records": action_records,
    }
