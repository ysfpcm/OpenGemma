"""Data contracts shared by the local retrieval subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class VectorChunk:
    """A document chunk ready to be persisted in a vector store."""

    point_id: str
    document_id: str
    text: str
    vector: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IngestResult:
    """Outcome of ingesting one local document."""

    document_id: str
    source: str
    chunks_stored: int
    duplicate: bool = False


@dataclass(slots=True)
class SearchResult:
    """A relevant context chunk returned by semantic search."""

    text: str
    score: float
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)


__all__ = ["IngestResult", "SearchResult", "VectorChunk"]
