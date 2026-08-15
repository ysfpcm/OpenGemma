"""Local embedding construction for RAG.

The conversational inference engine is deliberately absent from this module.
RAG uses a dedicated sentence-transformer model through OpenJarvis's existing
generic embedding contract.
"""

from __future__ import annotations

from functools import lru_cache

from openjarvis.tools.storage.embeddings import Embedder, SentenceTransformerEmbedder


@lru_cache(maxsize=4)
def get_local_embedder(model_name: str) -> Embedder:
    """Return a process-cached local embedder for *model_name*.

    Loading sentence-transformer weights is relatively expensive. Caching by
    model name keeps ingestion and retrieval lightweight without introducing a
    dependency on the active Qwen, Gemma, Ollama, or other generation model.
    """

    return SentenceTransformerEmbedder(model_name)


__all__ = ["Embedder", "get_local_embedder"]
