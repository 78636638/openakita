from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import settings
from .models import LearningCase


@dataclass(slots=True)
class LearningHitStat:
    target_type: str
    target_id: str
    related_case_id: str
    memory_id: str
    hit_count: int
    last_hit_at: str
    last_query: str
    last_source: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class LearningRunRecord:
    run_id: str
    trigger_source: str
    started_at: str
    finished_at: str | None
    status: str
    summary: dict[str, Any]


@dataclass(slots=True)
class LearningCreditStat:
    target_type: str
    target_id: str
    related_case_id: str
    memory_id: str
    helpful_count: int
    neutral_count: int
    harmful_count: int
    last_outcome: str
    last_source: str
    last_score: float
    last_hit_count: int
    context_ref: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class LearningCheckpointRecord:
    checkpoint_id: str
    action_id: str
    target_path: str
    backup_path: str
    exists_before: bool
    created_at: str


@dataclass(slots=True)
class LearningActionRecord:
    action_id: str
    case_id: str
    action_type: str
    target_path: str
    status: str
    mode: str
    checkpoint_id: str | None
    summary: dict[str, Any]
    created_at: str
    updated_at: str


class LearningStore:
    def __init__(self, db_path: Path | None = None) -> None:
        default_path = settings.project_root / "data" / "learning" / "openakita_learning.db"
        self._db_path = Path(db_path or default_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_cases (
                    case_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_ref TEXT NOT NULL UNIQUE,
                    case_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    session_id TEXT,
                    conversation_id TEXT,
                    trace_id TEXT,
                    task_id TEXT,
                    workspace_id TEXT,
                    user_id TEXT,
                    problem_summary TEXT NOT NULL,
                    outcome_summary TEXT NOT NULL,
                    root_cause TEXT,
                    harness_gap TEXT,
                    metrics_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    candidate_actions_json TEXT NOT NULL,
                    lineage_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    review_note TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT,
                    memory_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_cases_created_at ON learning_cases(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_cases_status ON learning_cases(status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_cases_domain ON learning_cases(domain, case_type, created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_runs (
                    run_id TEXT PRIMARY KEY,
                    trigger_source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_hit_stats (
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    related_case_id TEXT NOT NULL DEFAULT '',
                    memory_id TEXT NOT NULL DEFAULT '',
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_hit_at TEXT NOT NULL,
                    last_query TEXT NOT NULL DEFAULT '',
                    last_source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (target_type, target_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_hit_stats_type ON learning_hit_stats(target_type, updated_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_credit_stats (
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    related_case_id TEXT NOT NULL DEFAULT '',
                    memory_id TEXT NOT NULL DEFAULT '',
                    helpful_count INTEGER NOT NULL DEFAULT 0,
                    neutral_count INTEGER NOT NULL DEFAULT 0,
                    harmful_count INTEGER NOT NULL DEFAULT 0,
                    last_outcome TEXT NOT NULL DEFAULT '',
                    last_source TEXT NOT NULL DEFAULT '',
                    last_score REAL NOT NULL DEFAULT 0,
                    last_hit_count INTEGER NOT NULL DEFAULT 0,
                    context_ref TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (target_type, target_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_credit_stats_type ON learning_credit_stats(target_type, updated_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    exists_before INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_action_records (
                    action_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    checkpoint_id TEXT,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_action_records_case ON learning_action_records(case_id, updated_at)"
            )
            conn.commit()

    def upsert_case(self, case: LearningCase) -> tuple[str, bool]:
        record = case.to_record()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT case_id FROM learning_cases WHERE source_ref = ?",
                (record["source_ref"],),
            ).fetchone()
            if existing:
                return str(existing["case_id"]), False
            conn.execute(
                """
                INSERT INTO learning_cases (
                    case_id, source, source_ref, case_type, severity, domain,
                    session_id, conversation_id, trace_id, task_id, workspace_id, user_id,
                    problem_summary, outcome_summary, root_cause, harness_gap,
                    metrics_json, evidence_json, tags_json, candidate_actions_json, lineage_json,
                    status, review_note, reviewed_at, memory_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', NULL, '[]', ?)
                """,
                (
                    record["case_id"],
                    record["source"],
                    record["source_ref"],
                    record["case_type"],
                    record["severity"],
                    record["domain"],
                    record["session_id"],
                    record["conversation_id"],
                    record["trace_id"],
                    record["task_id"],
                    record["workspace_id"],
                    record["user_id"],
                    record["problem_summary"],
                    record["outcome_summary"],
                    record["root_cause"],
                    record["harness_gap"],
                    json.dumps(record["metrics"], ensure_ascii=False),
                    json.dumps(record["evidence"], ensure_ascii=False),
                    json.dumps(record["tags"], ensure_ascii=False),
                    json.dumps(record["candidate_actions"], ensure_ascii=False),
                    json.dumps(record["lineage"], ensure_ascii=False),
                    record["created_at"],
                ),
            )
            conn.commit()
        return record["case_id"], True

    def list_pending_review(self, *, limit: int = 100) -> list[LearningCase]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_cases WHERE reviewed_at IS NULL ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_case(row) for row in rows]

    def mark_reviewed(
        self,
        case_id: str,
        *,
        memory_ids: list[str] | None = None,
        note: str = "",
        written: bool = False,
    ) -> None:
        status = "written" if written else "skipped"
        with self._connect() as conn:
            conn.execute(
                "UPDATE learning_cases SET status = ?, review_note = ?, reviewed_at = ?, memory_ids_json = ? WHERE case_id = ?",
                (status, note, datetime.now().isoformat(), json.dumps(memory_ids or [], ensure_ascii=False), case_id),
            )
            conn.commit()

    def count_similar_problem(
        self,
        problem_summary: str,
        *,
        domain: str = "",
        lookback_days: int = 7,
    ) -> int:
        since = (datetime.now() - timedelta(days=lookback_days)).isoformat()
        query = "SELECT COUNT(*) AS c FROM learning_cases WHERE problem_summary = ? AND created_at >= ?"
        params: list[Any] = [problem_summary, since]
        if domain:
            query += " AND domain = ?"
            params.append(domain)
        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return int(row["c"]) if row else 0

    def list_cases(
        self,
        *,
        limit: int = 100,
        source: str | None = None,
    ) -> list[LearningCase]:
        query = "SELECT * FROM learning_cases"
        params: list[Any] = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_case(row) for row in rows]

    def get_latest_case(self, *, source: str | None = None) -> LearningCase | None:
        query = "SELECT * FROM learning_cases"
        params: list[Any] = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return self._row_to_case(row) if row else None

    def list_cases_for_planning(self, *, limit: int = 100) -> list[LearningCase]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM learning_cases
                WHERE reviewed_at IS NOT NULL
                  AND candidate_actions_json = '[]'
                  AND source != 'orchestration_result'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_case(row) for row in rows]

    def list_cases_with_candidate_actions(self, *, limit: int = 100) -> list[LearningCase]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM learning_cases
                WHERE candidate_actions_json != '[]'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_case(row) for row in rows]

    def list_reviewed_cases_with_candidate_actions(self, *, limit: int = 100) -> list[LearningCase]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM learning_cases
                WHERE candidate_actions_json != '[]'
                  AND reviewed_at IS NOT NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_case(row) for row in rows]

    def list_candidate_experience_pool(self, *, limit: int = 100) -> list[LearningCase]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c1.*
                FROM learning_cases c1
                WHERE c1.status = 'skipped'
                  AND c1.case_type = 'success'
                  AND c1.review_note LIKE 'candidate_experience_pool%'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM learning_cases c2
                    WHERE c2.status = 'written'
                      AND c2.problem_summary = c1.problem_summary
                      AND c2.domain = c1.domain
                  )
                ORDER BY c1.reviewed_at DESC, c1.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_case(row) for row in rows]

    def record_candidate_action_hits(
        self,
        usages: list[dict[str, Any]],
        *,
        query: str = "",
        source: str = "",
    ) -> int:
        normalized = [
            item
            for item in (usages or [])
            if isinstance(item, dict) and str(item.get("target_id", "") or "").strip()
        ]
        if not normalized:
            return 0

        recorded = 0
        now = datetime.now().isoformat()
        with self._connect() as conn:
            for item in normalized:
                target_id = str(item.get("target_id", "") or "").strip()
                if not target_id:
                    continue
                self._upsert_hit_stat(
                    conn,
                    target_type="candidate_action",
                    target_id=target_id,
                    related_case_id=str(item.get("case_id", "") or ""),
                    memory_id="",
                    query=query,
                    source=source,
                    now=now,
                )
                recorded += 1
            conn.commit()
        return recorded

    def get_case_by_source_ref(self, source_ref: str) -> LearningCase | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_cases WHERE source_ref = ?",
                (source_ref,),
            ).fetchone()
        return self._row_to_case(row) if row else None

    def get_case(self, case_id: str) -> LearningCase | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
        return self._row_to_case(row) if row else None

    def replace_candidate_actions(
        self,
        case_id: str,
        actions: list[dict[str, Any]],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE learning_cases
                SET candidate_actions_json = ?
                WHERE case_id = ?
                """,
                (json.dumps(actions, ensure_ascii=False), case_id),
            )
            conn.commit()

    def record_checkpoint(
        self,
        *,
        checkpoint_id: str,
        action_id: str,
        target_path: str,
        backup_path: str,
        exists_before: bool,
        created_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_checkpoints (
                    checkpoint_id, action_id, target_path, backup_path, exists_before, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    action_id,
                    target_path,
                    backup_path,
                    1 if exists_before else 0,
                    created_at,
                ),
            )
            conn.commit()

    def get_checkpoint(self, checkpoint_id: str) -> LearningCheckpointRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        return self._row_to_checkpoint(row) if row else None

    def record_action_execution(
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
    ) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_action_records (
                    action_id, case_id, action_type, target_path, status, mode,
                    checkpoint_id, summary_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM learning_action_records WHERE action_id = ?), ?),
                    ?
                )
                """,
                (
                    action_id,
                    case_id,
                    action_type,
                    target_path,
                    status,
                    mode,
                    checkpoint_id,
                    json.dumps(summary, ensure_ascii=False),
                    action_id,
                    now,
                    now,
                ),
            )
            conn.commit()

    def get_action_record(self, action_id: str) -> LearningActionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_action_records WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        return self._row_to_action_record(row) if row else None

    def has_action_record(self, action_id: str) -> bool:
        return self.get_action_record(action_id) is not None

    def list_action_records(
        self,
        *,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[LearningActionRecord]:
        query = "SELECT * FROM learning_action_records"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_action_record(row) for row in rows]

    def record_run(
        self,
        *,
        run_id: str,
        trigger_source: str,
        status: str,
        summary: dict[str, Any],
        started_at: str,
        finished_at: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_runs (
                    run_id, trigger_source, started_at, finished_at, status, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    trigger_source,
                    started_at,
                    finished_at,
                    status,
                    json.dumps(summary, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_latest_run(self, trigger_source: str | None = None) -> LearningRunRecord | None:
        query = "SELECT * FROM learning_runs"
        params: list[Any] = []
        if trigger_source:
            query += " WHERE trigger_source = ?"
            params.append(trigger_source)
        query += " ORDER BY started_at DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return self._row_to_run(row) if row else None

    def record_memory_hits(
        self,
        memory_ids: list[str],
        *,
        query: str = "",
        source: str = "",
    ) -> int:
        unique_ids = [mid for mid in dict.fromkeys(str(mid).strip() for mid in memory_ids) if mid]
        if not unique_ids:
            return 0
        now = datetime.now().isoformat()
        recorded = 0
        with self._connect() as conn:
            for memory_id in unique_ids:
                self._upsert_hit_stat(
                    conn,
                    target_type="memory",
                    target_id=memory_id,
                    related_case_id="",
                    memory_id=memory_id,
                    query=query,
                    source=source,
                    now=now,
                )
                recorded += 1
                for case_id in self._find_case_ids_for_memory(conn, memory_id):
                    self._upsert_hit_stat(
                        conn,
                        target_type="case",
                        target_id=case_id,
                        related_case_id=case_id,
                        memory_id=memory_id,
                        query=query,
                        source=source,
                        now=now,
                    )
                    recorded += 1
            conn.commit()
        return recorded

    def get_hit_stat(self, target_type: str, target_id: str) -> LearningHitStat | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM learning_hit_stats
                WHERE target_type = ? AND target_id = ?
                """,
                (target_type, target_id),
            ).fetchone()
        return self._row_to_hit_stat(row) if row else None

    def list_hit_stats(
        self,
        *,
        target_type: str | None = None,
        limit: int = 100,
    ) -> list[LearningHitStat]:
        query = "SELECT * FROM learning_hit_stats"
        params: list[Any] = []
        if target_type:
            query += " WHERE target_type = ?"
            params.append(target_type)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_hit_stat(row) for row in rows]

    def get_hit_count(self, target_type: str, target_id: str) -> int:
        stat = self.get_hit_stat(target_type, target_id)
        return stat.hit_count if stat else 0

    def summarize_hit_stats(
        self,
        *,
        target_type: str | None = None,
        top_n: int = 5,
    ) -> dict[str, Any]:
        query = "SELECT target_type, COUNT(*) AS target_count, COALESCE(SUM(hit_count), 0) AS total_hits FROM learning_hit_stats"
        params: list[Any] = []
        if target_type:
            query += " WHERE target_type = ?"
            params.append(target_type)
        query += " GROUP BY target_type"

        top_query = "SELECT * FROM learning_hit_stats"
        top_params: list[Any] = []
        if target_type:
            top_query += " WHERE target_type = ?"
            top_params.append(target_type)
        top_query += " ORDER BY hit_count DESC, updated_at DESC LIMIT ?"
        top_params.append(top_n)

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            top_rows = conn.execute(top_query, tuple(top_params)).fetchall()

        by_type = {
            str(row["target_type"]): {
                "target_count": int(row["target_count"]),
                "total_hits": int(row["total_hits"]),
            }
            for row in rows
        }
        top_hits = [
            {
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "related_case_id": row["related_case_id"],
                "memory_id": row["memory_id"],
                "hit_count": int(row["hit_count"]),
                "last_source": row["last_source"],
            }
            for row in top_rows
        ]
        return {"by_type": by_type, "top_hits": top_hits}

    def record_credit_observation(
        self,
        *,
        target_type: str,
        target_id: str,
        outcome: str,
        source: str,
        score: float,
        hit_count: int = 0,
        related_case_id: str = "",
        memory_id: str = "",
        context_ref: str = "",
    ) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT helpful_count, neutral_count, harmful_count, created_at
                FROM learning_credit_stats
                WHERE target_type = ? AND target_id = ?
                """,
                (target_type, target_id),
            ).fetchone()
            helpful = 1 if outcome == "helpful" else 0
            neutral = 1 if outcome == "neutral" else 0
            harmful = 1 if outcome == "harmful" else 0
            if existing:
                conn.execute(
                    """
                    UPDATE learning_credit_stats
                    SET related_case_id = ?, memory_id = ?,
                        helpful_count = ?, neutral_count = ?, harmful_count = ?,
                        last_outcome = ?, last_source = ?, last_score = ?,
                        last_hit_count = ?, context_ref = ?, updated_at = ?
                    WHERE target_type = ? AND target_id = ?
                    """,
                    (
                        related_case_id,
                        memory_id,
                        int(existing["helpful_count"]) + helpful,
                        int(existing["neutral_count"]) + neutral,
                        int(existing["harmful_count"]) + harmful,
                        outcome,
                        source,
                        float(score),
                        hit_count,
                        context_ref,
                        now,
                        target_type,
                        target_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO learning_credit_stats (
                        target_type, target_id, related_case_id, memory_id,
                        helpful_count, neutral_count, harmful_count,
                        last_outcome, last_source, last_score, last_hit_count,
                        context_ref, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_type,
                        target_id,
                        related_case_id,
                        memory_id,
                        helpful,
                        neutral,
                        harmful,
                        outcome,
                        source,
                        float(score),
                        hit_count,
                        context_ref,
                        now,
                        now,
                    ),
                )
            conn.commit()

    def record_credit_for_hit_stats(
        self,
        *,
        outcome: str,
        source: str,
        score: float,
        context_ref: str = "",
        limit: int = 100,
    ) -> int:
        observed = 0
        for hit in self.list_hit_stats(limit=limit):
            self.record_credit_observation(
                target_type=hit.target_type,
                target_id=hit.target_id,
                outcome=outcome,
                source=source,
                score=score,
                hit_count=hit.hit_count,
                related_case_id=hit.related_case_id,
                memory_id=hit.memory_id,
                context_ref=context_ref,
            )
            observed += 1
        return observed

    def get_credit_stat(self, target_type: str, target_id: str) -> LearningCreditStat | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM learning_credit_stats
                WHERE target_type = ? AND target_id = ?
                """,
                (target_type, target_id),
            ).fetchone()
        return self._row_to_credit_stat(row) if row else None

    def list_credit_stats(
        self,
        *,
        target_type: str | None = None,
        limit: int = 100,
    ) -> list[LearningCreditStat]:
        query = "SELECT * FROM learning_credit_stats"
        params: list[Any] = []
        if target_type:
            query += " WHERE target_type = ?"
            params.append(target_type)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_credit_stat(row) for row in rows]

    def summarize_credit_stats(
        self,
        *,
        target_type: str | None = None,
        top_n: int = 5,
    ) -> dict[str, Any]:
        query = """
            SELECT
                target_type,
                COUNT(*) AS target_count,
                COALESCE(SUM(helpful_count), 0) AS helpful_count,
                COALESCE(SUM(neutral_count), 0) AS neutral_count,
                COALESCE(SUM(harmful_count), 0) AS harmful_count
            FROM learning_credit_stats
        """
        params: list[Any] = []
        if target_type:
            query += " WHERE target_type = ?"
            params.append(target_type)
        query += " GROUP BY target_type"

        top_query = "SELECT * FROM learning_credit_stats"
        top_params: list[Any] = []
        if target_type:
            top_query += " WHERE target_type = ?"
            top_params.append(target_type)
        top_query += """
            ORDER BY
                harmful_count DESC,
                helpful_count DESC,
                updated_at DESC
            LIMIT ?
        """
        top_params.append(top_n)

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            top_rows = conn.execute(top_query, tuple(top_params)).fetchall()

        by_type = {
            str(row["target_type"]): {
                "target_count": int(row["target_count"]),
                "helpful_count": int(row["helpful_count"]),
                "neutral_count": int(row["neutral_count"]),
                "harmful_count": int(row["harmful_count"]),
            }
            for row in rows
        }
        top_credit = [
            {
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "last_outcome": row["last_outcome"],
                "last_score": float(row["last_score"]),
                "last_hit_count": int(row["last_hit_count"]),
                "helpful_count": int(row["helpful_count"]),
                "neutral_count": int(row["neutral_count"]),
                "harmful_count": int(row["harmful_count"]),
            }
            for row in top_rows
        ]
        return {"by_type": by_type, "top_credit": top_credit}

    def _find_case_ids_for_memory(self, conn: sqlite3.Connection, memory_id: str) -> list[str]:
        rows = conn.execute(
            """
            SELECT case_id, memory_ids_json
            FROM learning_cases
            WHERE memory_ids_json != '[]' AND memory_ids_json LIKE ?
            """,
            (f'%"{memory_id}"%',),
        ).fetchall()
        case_ids: list[str] = []
        for row in rows:
            memory_ids = json.loads(row["memory_ids_json"] or "[]")
            if memory_id in memory_ids:
                case_ids.append(str(row["case_id"]))
        return case_ids

    def _upsert_hit_stat(
        self,
        conn: sqlite3.Connection,
        *,
        target_type: str,
        target_id: str,
        related_case_id: str,
        memory_id: str,
        query: str,
        source: str,
        now: str,
    ) -> None:
        existing = conn.execute(
            """
            SELECT hit_count, created_at
            FROM learning_hit_stats
            WHERE target_type = ? AND target_id = ?
            """,
            (target_type, target_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE learning_hit_stats
                SET related_case_id = ?, memory_id = ?, hit_count = ?, last_hit_at = ?,
                    last_query = ?, last_source = ?, updated_at = ?
                WHERE target_type = ? AND target_id = ?
                """,
                (
                    related_case_id,
                    memory_id,
                    int(existing["hit_count"]) + 1,
                    now,
                    query,
                    source,
                    now,
                    target_type,
                    target_id,
                ),
            )
            return

        conn.execute(
            """
            INSERT INTO learning_hit_stats (
                target_type, target_id, related_case_id, memory_id, hit_count,
                last_hit_at, last_query, last_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                target_type,
                target_id,
                related_case_id,
                memory_id,
                now,
                query,
                source,
                now,
                now,
            ),
        )

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> LearningCase:
        return LearningCase.from_record(
            {
                "case_id": row["case_id"],
                "source": row["source"],
                "source_ref": row["source_ref"],
                "case_type": row["case_type"],
                "severity": row["severity"],
                "domain": row["domain"],
                "session_id": row["session_id"],
                "conversation_id": row["conversation_id"],
                "trace_id": row["trace_id"],
                "task_id": row["task_id"],
                "workspace_id": row["workspace_id"],
                "user_id": row["user_id"],
                "problem_summary": row["problem_summary"],
                "outcome_summary": row["outcome_summary"],
                "root_cause": row["root_cause"],
                "harness_gap": row["harness_gap"],
                "metrics": json.loads(row["metrics_json"] or "{}"),
                "evidence": json.loads(row["evidence_json"] or "[]"),
                "tags": json.loads(row["tags_json"] or "[]"),
                "candidate_actions": json.loads(row["candidate_actions_json"] or "[]"),
                "lineage": json.loads(row["lineage_json"] or "{}"),
                "status": row["status"],
                "review_note": row["review_note"],
                "reviewed_at": row["reviewed_at"],
                "memory_ids": json.loads(row["memory_ids_json"] or "[]"),
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _row_to_hit_stat(row: sqlite3.Row) -> LearningHitStat:
        return LearningHitStat(
            target_type=row["target_type"],
            target_id=row["target_id"],
            related_case_id=row["related_case_id"],
            memory_id=row["memory_id"],
            hit_count=int(row["hit_count"]),
            last_hit_at=row["last_hit_at"],
            last_query=row["last_query"],
            last_source=row["last_source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> LearningRunRecord:
        return LearningRunRecord(
            run_id=row["run_id"],
            trigger_source=row["trigger_source"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            summary=json.loads(row["summary_json"] or "{}"),
        )

    @staticmethod
    def _row_to_credit_stat(row: sqlite3.Row) -> LearningCreditStat:
        return LearningCreditStat(
            target_type=row["target_type"],
            target_id=row["target_id"],
            related_case_id=row["related_case_id"],
            memory_id=row["memory_id"],
            helpful_count=int(row["helpful_count"]),
            neutral_count=int(row["neutral_count"]),
            harmful_count=int(row["harmful_count"]),
            last_outcome=row["last_outcome"],
            last_source=row["last_source"],
            last_score=float(row["last_score"]),
            last_hit_count=int(row["last_hit_count"]),
            context_ref=row["context_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_checkpoint(row: sqlite3.Row) -> LearningCheckpointRecord:
        return LearningCheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            action_id=row["action_id"],
            target_path=row["target_path"],
            backup_path=row["backup_path"],
            exists_before=bool(row["exists_before"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_action_record(row: sqlite3.Row) -> LearningActionRecord:
        return LearningActionRecord(
            action_id=row["action_id"],
            case_id=row["case_id"],
            action_type=row["action_type"],
            target_path=row["target_path"],
            status=row["status"],
            mode=row["mode"],
            checkpoint_id=row["checkpoint_id"],
            summary=json.loads(row["summary_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
