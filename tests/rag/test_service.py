"""End-to-end tests for local RAG using Qdrant's embedded mode."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from openjarvis.core.config import RAGConfig  # noqa: E402
from openjarvis.rag.service import RAGService  # noqa: E402
from openjarvis.rag.vector_store import (  # noqa: E402
    QdrantVectorStore,
    VectorStoreUnavailableError,
)
from openjarvis.tools.storage.embeddings import Embedder  # noqa: E402


class SemanticTestEmbedder(Embedder):
    """Small deterministic semantic model for fast, offline service tests."""

    _LIGHT_WORDS = {
        "connectivity",
        "disconnecting",
        "integration",
        "light",
        "smart",
        "unavailable",
    }
    _FOOD_WORDS = {"cook", "cooking", "pasta", "recipe", "tomato"}

    def embed(self, texts: list[str]):  # type: ignore[no-untyped-def]
        vectors = []
        for text in texts:
            words = {word.strip(".,?!:#").lower() for word in text.split()}
            vectors.append(
                [
                    float(len(words & self._LIGHT_WORDS)),
                    float(len(words & self._FOOD_WORDS)),
                    0.01,
                ]
            )
        return vectors

    def dim(self) -> int:
        return 3


@pytest.fixture()
def service(tmp_path: Path) -> RAGService:
    config = RAGConfig(
        qdrant_path=":memory:",
        collection_name="rag_tests",
        knowledge_dir=str(tmp_path / "knowledge"),
        chunk_size=12,
        chunk_overlap=2,
        retrieval_limit=5,
    )
    store = QdrantVectorStore(
        local_path=":memory:",
        collection_name=config.collection_name,
    )
    return RAGService(config, embedder=SemanticTestEmbedder(), vector_store=store)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_document_ingestion_and_chunk_creation(
    service: RAGService, tmp_path: Path
) -> None:
    path = _write(
        tmp_path / "long.md",
        " ".join(f"word-{index}" for index in range(35)),
    )

    result = service.ingest_document(path)

    assert result.chunks_stored >= 3
    assert result.document_id.startswith("local:")
    assert result.source == str(path.resolve())


def test_duplicate_document_is_skipped(service: RAGService, tmp_path: Path) -> None:
    path = _write(tmp_path / "note.txt", "A short local knowledge note.")

    first = service.ingest_document(path)
    second = service.ingest_document(path)

    assert first.chunks_stored == 1
    assert not first.duplicate
    assert second.chunks_stored == 0
    assert second.duplicate
    assert second.document_id == first.document_id


def test_semantic_retrieval_ranks_rephrased_question(
    service: RAGService, tmp_path: Path
) -> None:
    _write(
        tmp_path / "light.md",
        "The living room light occasionally becomes unavailable. "
        "Reloading the Smart Life integration restored connectivity.",
    )
    _write(
        tmp_path / "recipe.md",
        "Cook pasta with tomato sauce for a quick dinner recipe.",
    )
    service.ingest_document(tmp_path / "light.md")
    service.ingest_document(tmp_path / "recipe.md")

    results = service.search_memory(
        "Why does my living room light keep disconnecting?", limit=2
    )

    assert len(results) == 2
    assert results[0].source == str((tmp_path / "light.md").resolve())
    assert "connectivity" in results[0].text
    assert results[0].score > results[1].score


def test_metadata_and_category_are_preserved(
    service: RAGService, tmp_path: Path
) -> None:
    service.initialize_knowledge_directory()
    path = _write(
        service.knowledge_dir / "networking" / "router.md",
        "The network router connectivity light becomes unavailable.",
    )
    ingest = service.ingest_document(path, metadata={"owner": "Marc"})

    result = service.search_memory("network connectivity light", limit=1)[0]

    assert result.metadata["document_id"] == ingest.document_id
    assert result.metadata["filename"] == "router.md"
    assert result.metadata["category"] == "networking"
    assert result.metadata["chunk_index"] == 0
    assert result.metadata["owner"] == "Marc"
    assert result.metadata["created_at"]
    assert result.metadata["source"] == str(path.resolve())


def test_delete_document_removes_all_chunks(
    service: RAGService, tmp_path: Path
) -> None:
    path = _write(
        tmp_path / "delete-me.md",
        "The living room light has a Smart Life connectivity issue.",
    )
    ingest = service.ingest_document(path)

    assert service.delete_document(ingest.document_id)
    assert not service.delete_document(ingest.document_id)
    assert service.search_memory("living room light") == []


def test_empty_knowledge_base_returns_empty(service: RAGService) -> None:
    assert service.search_memory("anything at all") == []
    assert service.search_memory("") == []


def test_initialize_knowledge_directory_creates_starter_categories(
    service: RAGService,
) -> None:
    root = service.initialize_knowledge_directory()

    assert (root / "ophanim").is_dir()
    assert (root / "home_assistant").is_dir()
    assert (root / "networking").is_dir()
    assert (root / "troubleshooting").is_dir()
    assert (root / "personal_notes").is_dir()


def test_unavailable_qdrant_has_actionable_error(tmp_path: Path) -> None:
    config = RAGConfig(
        qdrant_host="127.0.0.1",
        qdrant_port=1,
        qdrant_timeout=0.1,
        knowledge_dir=str(tmp_path / "knowledge"),
    )
    service = RAGService(config, embedder=SemanticTestEmbedder())

    with pytest.raises(VectorStoreUnavailableError, match="Qdrant is unavailable"):
        service.search_memory("living room light")
