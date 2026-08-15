"""Configuration coverage for the RAG subsystem."""

from __future__ import annotations

from openjarvis.core.config import (
    HardwareInfo,
    RAGConfig,
    generate_default_toml,
    load_config,
    validate_config_key,
)


def test_rag_defaults_are_local() -> None:
    config = RAGConfig()

    assert config.qdrant_host in {"127.0.0.1", "localhost"}
    assert config.qdrant_port == 6333
    assert "MiniLM" in config.embedding_model
    assert config.retrieval_limit == 5


def test_loads_rag_toml(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text(
        """[rag]
embedding_model = "custom-local-model"
qdrant_port = 7333
collection_name = "ophanim_docs"
chunk_size = 200
chunk_overlap = 25
retrieval_limit = 7
""",
        encoding="utf-8",
    )
    load_config.cache_clear()

    config = load_config(path)

    assert config.rag.embedding_model == "custom-local-model"
    assert config.rag.qdrant_port == 7333
    assert config.rag.collection_name == "ophanim_docs"
    assert config.rag.chunk_size == 200
    assert config.rag.chunk_overlap == 25
    assert config.rag.retrieval_limit == 7
    load_config.cache_clear()


def test_rag_keys_are_settable_and_documented() -> None:
    assert validate_config_key("rag.qdrant_port") is int
    assert validate_config_key("rag.embedding_model") is str
    assert "[rag]" in generate_default_toml(HardwareInfo())
