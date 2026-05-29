from __future__ import annotations

from openakita.memory.retrieval import RetrievalCandidate, RetrievalEngine


def test_retrieval_engine_emits_hit_callback_for_ranked_candidates(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def recorder(query: str, candidates: list[RetrievalCandidate], source: str) -> None:
        captured["query"] = query
        captured["source"] = source
        captured["ids"] = [c.memory_id for c in candidates]

    engine = RetrievalEngine(object(), hit_recorder=recorder)
    candidate = RetrievalCandidate(
        memory_id="mem-001",
        content="learning memory",
        memory_type="error",
        source_type="semantic",
        relevance=0.9,
        score=0.9,
    )

    monkeypatch.setattr(engine, "_decompose_query", lambda query, recent: {"keywords": [], "intent": "general"})
    monkeypatch.setattr(engine, "_build_enhanced_query", lambda query, recent, keywords: query)
    monkeypatch.setattr(engine, "_search_semantic", lambda query: [candidate])
    monkeypatch.setattr(engine, "_search_episodes", lambda query: [])
    monkeypatch.setattr(engine, "_search_recent", lambda days, query: [])
    monkeypatch.setattr(engine, "_search_attachments", lambda query, keywords, intent: [])
    monkeypatch.setattr(engine, "_merge_and_deduplicate", lambda *args: [candidate])
    monkeypatch.setattr(engine, "_rerank", lambda candidates, query, active_persona=None: candidates)

    ranked = engine.retrieve_candidates("记住上次失败经验", limit=5)

    assert ranked == [candidate]
    assert captured["query"] == "记住上次失败经验"
    assert captured["source"] == "search_memory"
    assert captured["ids"] == ["mem-001"]
