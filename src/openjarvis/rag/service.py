"""Small public service for ingesting and retrieving local knowledge."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from openjarvis.core.config import JarvisConfig, RAGConfig, load_config
from openjarvis.rag.embeddings import Embedder, get_local_embedder
from openjarvis.rag.models import IngestResult, SearchResult, VectorChunk
from openjarvis.rag.vector_store import QdrantVectorStore, VectorStore
from openjarvis.tools.storage.chunking import ChunkConfig, chunk_text
from openjarvis.tools.storage.ingest import read_document

logger = logging.getLogger(__name__)

_POINT_NAMESPACE = uuid.UUID("96a0ef30-eafb-4d8a-87b2-40e2fe85687e")
_KNOWLEDGE_CATEGORIES = (
    "ophanim",
    "home_assistant",
    "networking",
    "troubleshooting",
    "personal_notes",
)


class RAGService:
    """LLM-agnostic local document retrieval service."""

    def __init__(
        self,
        config: RAGConfig,
        *,
        embedder: Optional[Embedder] = None,
        vector_store: Optional[VectorStore] = None,
    ) -> None:
        if config.chunk_size <= 0:
            raise ValueError("rag.chunk_size must be greater than zero")
        if config.chunk_overlap < 0 or config.chunk_overlap >= config.chunk_size:
            raise ValueError(
                "rag.chunk_overlap must be non-negative and smaller than chunk_size"
            )
        self.config = config
        self.initialize_knowledge_directory()
        self._embedder = embedder or get_local_embedder(config.embedding_model)
        self._store = vector_store or QdrantVectorStore(
            host=config.qdrant_host,
            port=config.qdrant_port,
            collection_name=config.collection_name,
            local_path=config.qdrant_path,
            timeout=config.qdrant_timeout,
        )
        self._chunk_config = ChunkConfig(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
            min_chunk_size=1,
        )

    @property
    def knowledge_dir(self) -> Path:
        """Configured directory for manually managed local knowledge."""

        return Path(self.config.knowledge_dir).expanduser()

    def initialize_knowledge_directory(self) -> Path:
        """Create the lightweight starter taxonomy and return its root."""

        root = self.knowledge_dir
        for category in _KNOWLEDGE_CATEGORIES:
            (root / category).mkdir(parents=True, exist_ok=True)
        return root

    def _ensure_collection(self) -> None:
        self._store.ensure_collection(self._embedder.dim())

    @staticmethod
    def _document_id(path: Path) -> str:
        normalized = os.path.normcase(str(path.resolve()))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"local:{digest}"

    def _category_for(self, path: Path, category: str) -> str:
        if category.strip():
            return category.strip()
        try:
            relative = path.resolve().relative_to(self.knowledge_dir.resolve())
        except ValueError:
            return ""
        return relative.parts[0] if len(relative.parts) > 1 else ""

    @staticmethod
    def _vectors(raw_vectors: Any, expected: int) -> List[List[float]]:
        values = raw_vectors.tolist() if hasattr(raw_vectors, "tolist") else raw_vectors
        rows = [[float(value) for value in row] for row in values]
        if len(rows) != expected:
            raise ValueError(
                f"Embedding provider returned {len(rows)} vectors for {expected} chunks"
            )
        return rows

    def ingest_document(
        self,
        path: str | Path,
        *,
        category: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> IngestResult:
        """Read, chunk, embed, and store one local text/Markdown document."""

        source_path = Path(path).expanduser().resolve()
        supported_suffixes = {".txt", ".text", ".md", ".markdown", ".mdx"}
        if source_path.suffix.lower() not in supported_suffixes:
            raise ValueError(
                f"Unsupported RAG document type {source_path.suffix!r}; "
                "use a text or Markdown file"
            )

        text, document_meta = read_document(source_path)
        if not text.strip():
            raise ValueError(f"Cannot ingest an empty document: {source_path}")

        document_id = self._document_id(source_path)
        document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._ensure_collection()

        if self._store.document_hash(document_id) == document_hash:
            logger.info("RAG document already indexed: %s", source_path.name)
            return IngestResult(
                document_id=document_id,
                source=str(source_path),
                chunks_stored=0,
                duplicate=True,
            )

        chunks = chunk_text(
            text,
            source=str(source_path),
            config=self._chunk_config,
        )
        logger.info(
            "Creating embeddings for %d chunk(s) from %s",
            len(chunks),
            source_path.name,
        )
        rows = self._vectors(
            self._embedder.embed([chunk.content for chunk in chunks]),
            len(chunks),
        )
        created_at = datetime.now(timezone.utc).isoformat()
        inferred_category = self._category_for(source_path, category)

        vector_chunks: List[VectorChunk] = []
        for chunk, vector in zip(chunks, rows):
            chunk_metadata: Dict[str, Any] = dict(metadata or {})
            chunk_metadata.update(
                {
                    "document_id": document_id,
                    "document_hash": document_hash,
                    "source": str(source_path),
                    "filename": source_path.name,
                    "category": inferred_category,
                    "chunk_index": chunk.index,
                    "created_at": created_at,
                    "file_type": document_meta.file_type,
                    "size_bytes": document_meta.size_bytes,
                    "line_count": document_meta.line_count,
                }
            )
            point_id = str(uuid.uuid5(_POINT_NAMESPACE, f"{document_id}:{chunk.index}"))
            vector_chunks.append(
                VectorChunk(
                    point_id=point_id,
                    document_id=document_id,
                    text=chunk.content,
                    vector=vector,
                    metadata=chunk_metadata,
                )
            )

        # Only replace existing points after all new embeddings are ready.
        self._store.delete_document(document_id)
        self._store.upsert(vector_chunks)
        logger.info(
            "Indexed %s as %d RAG chunk(s)", source_path.name, len(vector_chunks)
        )
        return IngestResult(
            document_id=document_id,
            source=str(source_path),
            chunks_stored=len(vector_chunks),
        )

    def search_memory(
        self,
        query: str,
        *,
        limit: Optional[int] = None,
    ) -> List[SearchResult]:
        """Return semantic context for *query* without invoking an LLM."""

        query = (query or "").strip()
        if not query:
            return []
        result_limit = self.config.retrieval_limit if limit is None else int(limit)
        if result_limit <= 0:
            return []
        self._ensure_collection()
        vector = self._vectors(self._embedder.embed([query]), 1)[0]
        results = self._store.search(vector, limit=result_limit)
        logger.info("RAG search returned %d result(s)", len(results))
        return results

    def delete_document(self, document_id: str) -> bool:
        """Delete a document and all of its chunks."""

        if not document_id.strip():
            return False
        self._ensure_collection()
        deleted = self._store.delete_document(document_id)
        if deleted:
            logger.info("Deleted RAG document %s", document_id)
        return deleted

    def close(self) -> None:
        """Release vector-store resources."""

        self._store.close()

    def __enter__(self) -> "RAGService":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def build_rag_service(
    config: Optional[JarvisConfig] = None,
    *,
    embedder: Optional[Embedder] = None,
    vector_store: Optional[VectorStore] = None,
) -> RAGService:
    """Construct a RAG service from OpenJarvis configuration."""

    app_config = config or load_config()
    return RAGService(
        app_config.rag,
        embedder=embedder,
        vector_store=vector_store,
    )


_default_service: Optional[RAGService] = None
_default_service_lock = threading.Lock()


def get_rag_service() -> RAGService:
    """Return the process-wide default service, constructing it once."""

    global _default_service
    if _default_service is None:
        with _default_service_lock:
            if _default_service is None:
                _default_service = build_rag_service()
    return _default_service


def ingest_document(path: str | Path, **kwargs: Any) -> IngestResult:
    """Ingest a local text/Markdown file through the default service."""

    return get_rag_service().ingest_document(path, **kwargs)


def search_memory(query: str, limit: Optional[int] = None) -> List[SearchResult]:
    """Search local knowledge through the default service."""

    return get_rag_service().search_memory(query, limit=limit)


def delete_document(document_id: str) -> bool:
    """Delete a local knowledge document through the default service."""

    return get_rag_service().delete_document(document_id)


__all__ = [
    "RAGService",
    "build_rag_service",
    "delete_document",
    "get_rag_service",
    "ingest_document",
    "search_memory",
]
