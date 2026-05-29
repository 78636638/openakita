from __future__ import annotations

from .store import LearningStore


class LearningHitTracker:
    """Lightweight hit recorder for retrieved memories and related learning cases."""

    def __init__(self, store: LearningStore | None = None) -> None:
        self.store = store or LearningStore()

    def record_cited_memories(
        self,
        memories: list[dict],
        *,
        query: str = "",
        source: str = "",
    ) -> int:
        memory_ids = [str(m.get("id", "")).strip() for m in memories if isinstance(m, dict)]
        return self.store.record_memory_hits(memory_ids, query=query, source=source)
