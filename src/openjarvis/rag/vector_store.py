"""Replaceable vector-store contract and the local Qdrant implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Sequence

from openjarvis.rag.models import SearchResult, VectorChunk


class RAGError(RuntimeError):
    """Base error for the retrieval subsystem."""


class RAGDependencyError(RAGError):
    """Raised when an optional local RAG dependency is not installed."""


class VectorStoreUnavailableError(RAGError):
    """Raised when the configured vector database cannot be reached."""


class RAGConfigurationError(RAGError):
    """Raised when persisted vector data conflicts with current settings."""


class VectorStore(ABC):
    """Minimal backend contract needed by :class:`RAGService`."""

    @abstractmethod
    def ensure_collection(self, vector_size: int) -> None:
        """Ensure the target collection exists with *vector_size*."""

    @abstractmethod
    def document_hash(self, document_id: str) -> Optional[str]:
        """Return the stored content hash for a document, if present."""

    @abstractmethod
    def upsert(self, chunks: Sequence[VectorChunk]) -> None:
        """Insert or replace vector chunks."""

    @abstractmethod
    def search(self, vector: Sequence[float], *, limit: int) -> List[SearchResult]:
        """Return the nearest stored chunks."""

    @abstractmethod
    def delete_document(self, document_id: str) -> bool:
        """Delete every chunk for *document_id*."""

    def close(self) -> None:
        """Release backend resources, if any."""


class QdrantVectorStore(VectorStore):
    """Qdrant-backed local vector storage.

    By default this connects to a Qdrant server on the local machine. Setting
    ``local_path`` uses qdrant-client's embedded local mode instead; ``:memory:``
    is useful for tests and demos.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 6333,
        collection_name: str = "openjarvis_knowledge",
        local_path: str = "",
        timeout: float = 5.0,
        client: Any = None,
    ) -> None:
        self.collection_name = collection_name
        self._host = host
        self._port = int(port)
        self._local_path = local_path
        self._timeout = float(timeout)
        self._client = client
        self._models: Any = None

    def _load_qdrant(self) -> tuple[Any, Any]:
        if self._client is not None and self._models is not None:
            return self._client, self._models
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:
            raise RAGDependencyError(
                "qdrant-client is required for local RAG. "
                "Install it with: uv sync --extra rag"
            ) from exc

        if self._client is None:
            if self._local_path:
                if self._local_path == ":memory:":
                    self._client = QdrantClient(":memory:")
                else:
                    path = Path(self._local_path).expanduser()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._client = QdrantClient(path=str(path))
            else:
                host = self._host.rstrip("/")
                url = host if "://" in host else f"http://{host}:{self._port}"
                self._client = QdrantClient(
                    url=url,
                    timeout=self._timeout,
                    check_compatibility=False,
                )
        self._models = models
        return self._client, self._models

    def _filter(self, document_id: str) -> Any:
        _client, models = self._load_qdrant()
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            ]
        )

    def _unavailable(self, action: str, exc: Exception) -> VectorStoreUnavailableError:
        target = self._local_path or f"{self._host}:{self._port}"
        return VectorStoreUnavailableError(
            f"Qdrant is unavailable at {target} while {action}. "
            "Start the local Qdrant service or configure rag.qdrant_path."
        )

    def ensure_collection(self, vector_size: int) -> None:
        client, models = self._load_qdrant()
        try:
            exists = client.collection_exists(self.collection_name)
            if not exists:
                client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
                return
            info = client.get_collection(self.collection_name)
        except Exception as exc:  # qdrant has HTTP and local-client error families
            raise self._unavailable("ensuring the collection", exc) from exc

        vectors = info.config.params.vectors
        stored_size = getattr(vectors, "size", None)
        if stored_size is not None and int(stored_size) != int(vector_size):
            raise RAGConfigurationError(
                f"Qdrant collection {self.collection_name!r} uses vectors of "
                f"size {stored_size}, but the configured embedding model emits "
                f"{vector_size}. Use a new collection name or re-index it."
            )

    def document_hash(self, document_id: str) -> Optional[str]:
        client, _models = self._load_qdrant()
        try:
            points, _offset = client.scroll(
                collection_name=self.collection_name,
                scroll_filter=self._filter(document_id),
                limit=1,
                with_payload=["document_hash"],
                with_vectors=False,
            )
        except Exception as exc:
            raise self._unavailable("checking for duplicates", exc) from exc
        if not points:
            return None
        payload = points[0].payload or {}
        value = payload.get("document_hash")
        return str(value) if value else None

    def upsert(self, chunks: Sequence[VectorChunk]) -> None:
        if not chunks:
            return
        client, models = self._load_qdrant()
        points = [
            models.PointStruct(
                id=chunk.point_id,
                vector=list(chunk.vector),
                payload={
                    "text": chunk.text,
                    "document_id": chunk.document_id,
                    **chunk.metadata,
                },
            )
            for chunk in chunks
        ]
        try:
            client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )
        except Exception as exc:
            raise self._unavailable("storing vectors", exc) from exc

    def search(self, vector: Sequence[float], *, limit: int) -> List[SearchResult]:
        if limit <= 0:
            return []
        client, _models = self._load_qdrant()
        try:
            response = client.query_points(
                collection_name=self.collection_name,
                query=list(vector),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            raise self._unavailable("searching vectors", exc) from exc

        results: List[SearchResult] = []
        for point in response.points:
            payload = dict(point.payload or {})
            text = str(payload.pop("text", ""))
            source = str(payload.get("source", ""))
            results.append(
                SearchResult(
                    text=text,
                    score=float(point.score),
                    source=source,
                    metadata=payload,
                )
            )
        return results

    def delete_document(self, document_id: str) -> bool:
        client, models = self._load_qdrant()
        exists = self.document_hash(document_id) is not None
        if not exists:
            return False
        try:
            client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=self._filter(document_id)
                ),
                wait=True,
            )
        except Exception as exc:
            raise self._unavailable("deleting a document", exc) from exc
        return True

    def close(self) -> None:
        """Close the local or remote Qdrant client."""

        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = [
    "QdrantVectorStore",
    "RAGConfigurationError",
    "RAGDependencyError",
    "RAGError",
    "VectorStore",
    "VectorStoreUnavailableError",
]
