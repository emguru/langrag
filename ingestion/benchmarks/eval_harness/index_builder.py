"""Build in-memory index for eval harness corpora."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from eval_harness.baselines import BM25Index


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_harness_index(
    harness_manifest: dict[str, Any],
    *,
    harness_root: Path,
    fixtures_root: Path,
) -> tuple[list[dict], Any, dict[str, "BM25Index"]]:
    from ingestion.config import settings
    from ingestion.pipeline import chunk_documents, extract_text_from_bytes, get_embedder
    from ingestion.swag.profile import SWAGProfileGenerator

    from eval_harness.baselines import BM25Index

    subdir = harness_manifest.get("fixtures_root_subdir", "eval-harness")
    corpus_root = fixtures_root / subdir
    prior_mode = settings.swag_profile_mode
    settings.swag_profile_mode = "heuristic"
    embedder = get_embedder()
    generator = SWAGProfileGenerator()
    indexed: list[dict] = []
    bm25_by_topic: dict[str, BM25Index] = {}

    try:
        for corpus in harness_manifest.get("corpora") or []:
            manifest_path = harness_root / str(corpus["manifest"])
            corpus_manifest = load_json(manifest_path)
            topic = str(corpus_manifest.get("topic") or corpus.get("topic") or "")
            topic_docs: list[dict] = []
            for entry in corpus_manifest.get("fixtures") or []:
                seed_key = str(entry["seed_key"])
                rel_path = Path(str(entry["path"]))
                path = corpus_root / rel_path
                if not path.is_file():
                    raise FileNotFoundError(f"fixture missing: {path}")
                source = f"validation://{seed_key}"
                pages = extract_text_from_bytes(path.read_bytes(), path.name)
                chunks = chunk_documents(pages, source=source)
                if not chunks:
                    continue
                texts = [chunk["text"] for chunk in chunks]
                vectors = embedder.embed_documents(texts)
                for chunk, vector, text in zip(chunks, vectors, texts):
                    profile = generator.generate_chunk_profile(
                        chunk_text=text,
                        source_filename=seed_key.split("/")[-1],
                        page_number=chunk.get("page"),
                    )
                    row = {
                        "id": str(uuid.uuid4()),
                        "text": text,
                        "source": source,
                        "seed_key": seed_key,
                        "topic": topic,
                        "corpus_id": corpus.get("id"),
                        "page": chunk.get("page"),
                        "chunk_index": chunk.get("chunk_index"),
                        "score": 0.0,
                        "swag_profile": profile.model_dump() if profile else None,
                        "_vector": vector,
                    }
                    indexed.append(row)
                    topic_docs.append(row)
            if topic_docs:
                bm25_by_topic[topic] = BM25Index(topic_docs)
    finally:
        settings.swag_profile_mode = prior_mode

    return indexed, embedder, bm25_by_topic
