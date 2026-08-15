"""Optional real-model semantic retrieval smoke test."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("sentence_transformers")

from openjarvis.core.config import RAGConfig  # noqa: E402
from openjarvis.rag.service import RAGService  # noqa: E402
from openjarvis.rag.vector_store import QdrantVectorStore  # noqa: E402


@pytest.mark.hub
def test_real_local_embedding_semantics(tmp_path: Path) -> None:
    config = RAGConfig(
        qdrant_path=":memory:",
        collection_name="rag_real_semantics",
        knowledge_dir=str(tmp_path / "knowledge"),
    )
    service = RAGService(
        config,
        vector_store=QdrantVectorStore(
            local_path=":memory:", collection_name=config.collection_name
        ),
    )
    light = tmp_path / "light.md"
    light.write_text(
        "The living room light occasionally becomes unavailable. "
        "Reloading the Smart Life integration restored connectivity.",
        encoding="utf-8",
    )
    recipe = tmp_path / "recipe.md"
    recipe.write_text(
        "Boil pasta and serve it with tomato sauce and parmesan cheese.",
        encoding="utf-8",
    )
    service.ingest_document(light)
    service.ingest_document(recipe)

    results = service.search_memory(
        "Why does my living room light keep disconnecting?", limit=2
    )

    assert results[0].metadata["filename"] == "light.md"
    assert results[0].score > results[1].score
