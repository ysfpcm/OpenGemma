"""LLM-agnostic local retrieval for OpenJarvis/Ophanim."""

from openjarvis.rag.models import IngestResult, SearchResult
from openjarvis.rag.service import (
    RAGService,
    build_rag_service,
    delete_document,
    ingest_document,
    search_memory,
)
from openjarvis.rag.vector_store import (
    QdrantVectorStore,
    RAGConfigurationError,
    RAGDependencyError,
    RAGError,
    VectorStore,
    VectorStoreUnavailableError,
)

__all__ = [
    "IngestResult",
    "QdrantVectorStore",
    "RAGConfigurationError",
    "RAGDependencyError",
    "RAGError",
    "RAGService",
    "SearchResult",
    "VectorStore",
    "VectorStoreUnavailableError",
    "build_rag_service",
    "delete_document",
    "ingest_document",
    "search_memory",
]
