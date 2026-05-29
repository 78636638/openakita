from __future__ import annotations

from pathlib import Path

from openakita.learning.models import LearningCase
from openakita.learning.store import LearningStore


def test_learning_store_upsert_and_query(tmp_path: Path) -> None:
    store = LearningStore(db_path=tmp_path / "learning.db")
    case = LearningCase(
        source="self_check",
        source_ref="self_check:1:test_a",
        problem_summary="自检失败: test_a",
        outcome_summary="boom",
    )

    case_id, created = store.upsert_case(case)
    fetched = store.get_case_by_source_ref("self_check:1:test_a")

    assert created is True
    assert fetched is not None
    assert fetched.case_id == case_id
    assert fetched.problem_summary == "自检失败: test_a"


def test_learning_store_dedups_by_source_ref(tmp_path: Path) -> None:
    store = LearningStore(db_path=tmp_path / "learning.db")
    first = LearningCase(
        source="self_check",
        source_ref="self_check:1:test_dup",
        problem_summary="自检失败: test_dup",
        outcome_summary="boom",
    )
    second = LearningCase(
        source="self_check",
        source_ref="self_check:1:test_dup",
        problem_summary="自检失败: test_dup",
        outcome_summary="boom again",
    )

    _, created_first = store.upsert_case(first)
    case_id, created_second = store.upsert_case(second)

    assert created_first is True
    assert created_second is False
    assert len(store.list_cases()) == 1
    assert store.get_case_by_source_ref("self_check:1:test_dup").case_id == case_id


def test_learning_store_records_memory_and_case_hits(tmp_path: Path) -> None:
    store = LearningStore(db_path=tmp_path / "learning.db")
    case = LearningCase(
        source="self_check",
        source_ref="self_check:1:test_hit",
        problem_summary="自检失败: test_hit",
        outcome_summary="boom",
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-123"], note="written", written=True)

    recorded = store.record_memory_hits(["mem-123"], query="memory db", source="search_memory")
    memory_stat = store.get_hit_stat("memory", "mem-123")
    case_stat = store.get_hit_stat("case", case_id)

    assert recorded == 2
    assert memory_stat is not None
    assert memory_stat.hit_count == 1
    assert memory_stat.last_query == "memory db"
    assert memory_stat.last_source == "search_memory"
    assert case_stat is not None
    assert case_stat.hit_count == 1
    assert case_stat.related_case_id == case_id
    assert case_stat.memory_id == "mem-123"


def test_learning_store_summarizes_hit_stats_and_latest_run(tmp_path: Path) -> None:
    store = LearningStore(db_path=tmp_path / "learning.db")
    case = LearningCase(
        source="self_check",
        source_ref="self_check:1:test_summary",
        problem_summary="自检失败: test_summary",
        outcome_summary="boom",
    )
    case_id, _ = store.upsert_case(case)
    store.mark_reviewed(case_id, memory_ids=["mem-321"], note="written", written=True)
    store.record_memory_hits(["mem-321"], query="summary", source="auto_retrieval")
    store.record_run(
        run_id="run-1",
        trigger_source="system:test",
        status="ok",
        summary={"foo": "bar"},
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:01",
    )

    summary = store.summarize_hit_stats(top_n=5)
    latest = store.get_latest_run("system:test")

    assert summary["by_type"]["memory"]["total_hits"] == 1
    assert summary["by_type"]["case"]["total_hits"] == 1
    assert summary["top_hits"][0]["hit_count"] == 1
    assert latest is not None
    assert latest.run_id == "run-1"
    assert latest.summary == {"foo": "bar"}


def test_learning_store_records_and_summarizes_credit_stats(tmp_path: Path) -> None:
    store = LearningStore(db_path=tmp_path / "learning.db")
    store.record_credit_observation(
        target_type="memory",
        target_id="mem-1",
        outcome="helpful",
        source="daily_evaluation",
        score=0.82,
        hit_count=3,
        memory_id="mem-1",
        context_ref="report-1",
    )
    store.record_credit_observation(
        target_type="memory",
        target_id="mem-1",
        outcome="harmful",
        source="daily_evaluation",
        score=-0.35,
        hit_count=4,
        memory_id="mem-1",
        context_ref="report-2",
    )

    stat = store.get_credit_stat("memory", "mem-1")
    summary = store.summarize_credit_stats(top_n=5)

    assert stat is not None
    assert stat.helpful_count == 1
    assert stat.harmful_count == 1
    assert stat.neutral_count == 0
    assert stat.last_outcome == "harmful"
    assert stat.last_score == -0.35
    assert summary["by_type"]["memory"]["helpful_count"] == 1
    assert summary["by_type"]["memory"]["harmful_count"] == 1
    assert summary["top_credit"][0]["target_id"] == "mem-1"
