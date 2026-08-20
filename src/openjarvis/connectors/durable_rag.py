"""Durable, model-independent RAG index boundary for Phase 8.

This adapter makes restart and idempotent-ingestion guarantees explicit while
reusing the existing SQLite/FTS5 KnowledgeStore and TwoStageRetriever path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from openjarvis.tools.storage._stubs import RetrievalResult

from .retriever import TwoStageRetriever
from .store import KnowledgeStore


class DurableRagIndex:
    """Persistent RAG index with stable source identity and BM25 retrieval."""

    model_independent = True

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.store = KnowledgeStore(db_path=self.db_path)
        self.retriever = TwoStageRetriever(self.store, reranker=None)

    def ingest(
        self,
        content: str,
        *,
        source: str,
        source_id: str,
        chunk_index: int = 0,
        doc_type: str = "",
        title: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Ingest a stable chunk; retrying the same natural key is idempotent."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content is required for durable RAG ingestion")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required for durable RAG ingestion")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id is required for durable RAG ingestion")
        if isinstance(chunk_index, bool) or not isinstance(chunk_index, int):
            raise ValueError("chunk_index must be an integer")
        if chunk_index < 0:
            raise ValueError("chunk_index cannot be negative")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        existing = self._existing_chunk(source, source_id, chunk_index)
        if existing is not None:
            if existing["content"] != content:
                raise ValueError("conflicting content for an existing RAG identity")
            return str(existing["id"])
        chunk_id = self.store.store(
            content,
            source=source,
            source_id=source_id,
            doc_id=source_id,
            chunk_index=chunk_index,
            doc_type=doc_type,
            title=title,
            metadata=metadata,
        )
        existing = self._existing_chunk(source, source_id, chunk_index)
        if existing is not None:
            if existing["content"] != content:
                raise ValueError("conflicting content for an existing RAG identity")
            return str(existing["id"])
        return str(chunk_id)

    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievalResult]:
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ValueError("top_k must be an integer")
        if top_k < 0:
            raise ValueError("top_k cannot be negative")
        if top_k == 0:
            return []
        return self.retriever.retrieve(query, top_k=top_k)

    def _existing_chunk(
        self, source: str, source_id: str, chunk_index: int
    ) -> Optional[sqlite3.Row]:
        with sqlite3.connect(str(self.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT id, content FROM knowledge_chunks "
                "WHERE source=? AND source_id=? AND chunk_index=?",
                (source, source_id, chunk_index),
            ).fetchone()

    def chunk_count(self) -> int:
        with sqlite3.connect(str(self.db_path)) as connection:
            row = connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()
        return int(row[0])

    def ingest_many(self, documents: Iterable[dict[str, Any]]) -> list[str]:
        return [self.ingest(**document) for document in documents]

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "DurableRagIndex":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


__all__ = ["DurableRagIndex"]
