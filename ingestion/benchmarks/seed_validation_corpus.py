#!/usr/bin/env python3
"""Seed validation corpus from fixtures + manifest (stable seed_key in Qdrant)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parent


def recreate_collection(collection: str, qdrant_url: str) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    client = QdrantClient(url=qdrant_url)
    try:
        client.delete_collection(collection)
        print(f"deleted collection {collection}")
    except Exception as exc:
        print(f"delete skipped: {exc}")
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
    )
    print(f"created empty collection {collection}")


def seed_fixtures(manifest: dict, *, collection: str, fixtures_root: Path) -> list[dict]:
    from ingestion.pipeline import ingest_bytes

    results = []
    for entry in manifest.get("fixtures") or []:
        seed_key = str(entry["seed_key"])
        topic = str(entry["topic"])
        rel_path = Path(str(entry["path"]))
        path = fixtures_root / rel_path
        if not path.is_file():
            raise FileNotFoundError(f"fixture missing: {path}")
        data = path.read_bytes()
        out = ingest_bytes(
            data,
            path.name,
            topic,
            collection,
            seed_key=seed_key,
        )
        print(f"  fixture {seed_key}: {out['chunks_indexed']} chunks")
        results.append(out)
    return results


async def seed_urls(manifest: dict, *, collection: str) -> list[dict]:
    from ingestion.pipeline import chunk_documents, embed_and_upsert, extract_text_from_bytes, fetch_url
    import uuid

    results = []
    for entry in manifest.get("urls") or []:
        seed_key = str(entry["seed_key"])
        topic = str(entry["topic"])
        url = str(entry["url"])
        data, filename = await fetch_url(url)
        source = f"validation://{seed_key}"
        pages = extract_text_from_bytes(data, filename)
        chunks = chunk_documents(pages, source=source)
        for chunk in chunks:
            chunk["seed_key"] = seed_key
        doc_id = str(uuid.uuid4())
        count = embed_and_upsert(chunks, collection, topic, doc_id=doc_id)
        print(f"  url {seed_key}: {count} chunks from {url}")
        results.append(
            {
                "seed_key": seed_key,
                "chunks_indexed": count,
                "url": url,
                "topic": topic,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=BENCHMARKS / "validation_manifest.json",
    )
    parser.add_argument(
        "--fixtures-root",
        type=Path,
        required=True,
        help="Root directory containing fixture markdown files",
    )
    parser.add_argument("--collection", default="research_docs")
    parser.add_argument(
        "--qdrant-url",
        default="http://qdrant.rag.svc.cluster.local:6333",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Wipe and recreate the collection before seeding",
    )
    parser.add_argument("--skip-urls", action="store_true")
    args = parser.parse_args()

    with args.manifest.open(encoding="utf-8") as handle:
        manifest = json.load(handle)

    collection = args.collection or manifest.get("collection") or "research_docs"
    if args.recreate:
        recreate_collection(collection, args.qdrant_url)

    print("Seeding fixtures...")
    fixture_results = seed_fixtures(
        manifest,
        collection=collection,
        fixtures_root=args.fixtures_root,
    )
    url_results: list[dict] = []
    if not args.skip_urls:
        print("Seeding URLs...")
        url_results = asyncio.run(seed_urls(manifest, collection=collection))

    from qdrant_client import QdrantClient

    points = QdrantClient(url=args.qdrant_url).get_collection(collection).points_count
    summary = {
        "collection": collection,
        "fixtures": len(fixture_results),
        "urls": len(url_results),
        "points": points,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
