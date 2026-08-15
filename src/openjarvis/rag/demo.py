"""End-to-end local RAG demo.

Run with ``python -m openjarvis.rag.demo`` after installing the ``rag`` extra
and starting Qdrant (or configuring ``rag.qdrant_path`` for embedded mode).
"""

from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

from openjarvis.rag.service import build_rag_service

_SAMPLE = """# Living room light

The living room light occasionally becomes unavailable.
Reloading the Smart Life integration restored connectivity.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local RAG smoke test")
    parser.add_argument(
        "--query",
        default="Why does my living room light keep disconnecting?",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    service = build_rag_service()
    with tempfile.TemporaryDirectory(prefix="openjarvis-rag-demo-") as temp_dir:
        sample_path = Path(temp_dir) / "living-room.md"
        sample_path.write_text(_SAMPLE, encoding="utf-8")
        ingest = service.ingest_document(sample_path, category="troubleshooting")
        try:
            results = service.search_memory(args.query, limit=3)
            print(f"Ingested {ingest.chunks_stored} chunk(s) from {ingest.source}")
            print(f"Query: {args.query}\n")
            for index, result in enumerate(results, start=1):
                print(f"{index}. score={result.score:.4f}")
                print(f"   source={result.source}")
                print(f"   {result.text}\n")
        finally:
            service.delete_document(ingest.document_id)
            service.close()


if __name__ == "__main__":
    main()
