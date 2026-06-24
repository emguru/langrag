from __future__ import annotations

from collections import defaultdict

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from ingestion.config import settings
from ingestion.pipeline import get_embedder


def _client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def _normalize_collection(collection: str) -> str:
    name = collection or settings.qdrant_collection
    if name != settings.qdrant_collection:
        raise ValueError("Collection not allowed")
    return name


def collection_stats(collection: str = "") -> dict:
    collection = _normalize_collection(collection)
    client = _client()
    try:
        info = client.get_collection(collection)
        total_points = info.points_count or 0
    except Exception:
        return {
            "collection": collection,
            "total_points": 0,
            "document_count": 0,
            "topics": [],
        }

    topics: set[str] = set()
    sources: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=["topic", "source"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get("topic"):
                topics.add(str(payload["topic"]))
            if payload.get("source"):
                sources.add(str(payload["source"]))
        if offset is None:
            break

    return {
        "collection": collection,
        "total_points": total_points,
        "document_count": len(sources),
        "topics": sorted(topics),
    }


def list_documents(collection: str = "", topic: str | None = None) -> list[dict]:
    collection = _normalize_collection(collection)
    client = _client()
    grouped: dict[str, dict] = {}

    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if topic and payload.get("topic") != topic:
                continue
            source = str(payload.get("source", "unknown"))
            entry = grouped.setdefault(
                source,
                {
                    "source": source,
                    "topic": payload.get("topic"),
                    "document_id": payload.get("document_id"),
                    "chunk_count": 0,
                    "pages": set(),
                },
            )
            entry["chunk_count"] += 1
            if payload.get("page") is not None:
                entry["pages"].add(payload["page"])
        if offset is None:
            break

    documents = []
    for entry in grouped.values():
        pages = entry.pop("pages")
        entry["page_count"] = len(pages)
        documents.append(entry)
    documents.sort(key=lambda d: (str(d.get("topic") or ""), d["source"]))
    return documents


def list_chunks(
    collection: str = "",
    source: str | None = None,
    topic: str | None = None,
    limit: int = 50,
) -> list[dict]:
    collection = _normalize_collection(collection)
    client = _client()
    chunks: list[dict] = []
    offset = None

    while len(chunks) < limit:
        points, offset = client.scroll(
            collection_name=collection,
            limit=min(256, limit - len(chunks)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if source and payload.get("source") != source:
                continue
            if topic and payload.get("topic") != topic:
                continue
            chunks.append(
                {
                    "id": str(point.id),
                    "text": payload.get("text", ""),
                    "source": payload.get("source"),
                    "page": payload.get("page"),
                    "chunk_index": payload.get("chunk_index"),
                    "topic": payload.get("topic"),
                "document_id": payload.get("document_id"),
                "seed_key": payload.get("seed_key"),
            }
            )
            if len(chunks) >= limit:
                break
        if offset is None:
            break

    chunks.sort(key=lambda c: (c.get("page") or 0, c.get("chunk_index") or 0))
    return chunks


def search_documents(
    query: str,
    collection: str = "",
    top_k: int | None = None,
    topic: str | None = None,
) -> list[dict]:
    collection = _normalize_collection(collection)
    limit = top_k or settings.rag_top_k
    embedder = get_embedder()
    vectors = embedder.embed_documents([query])
    client = _client()

    query_filter = None
    if topic:
        query_filter = Filter(
            must=[FieldCondition(key="topic", match=MatchValue(value=topic))]
        )

    response = client.query_points(
        collection_name=collection,
        query=vectors[0],
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )

    hits = []
    for hit in response.points:
        payload = hit.payload or {}
        hits.append(
            {
                "id": str(hit.id),
                "score": hit.score,
                "text": payload.get("text", ""),
                "source": payload.get("source", "unknown"),
                "page": payload.get("page"),
                "chunk_index": payload.get("chunk_index"),
                "topic": payload.get("topic"),
                "document_id": payload.get("document_id"),
                "swag_profile": payload.get("swag_profile"),
                "seed_key": payload.get("seed_key"),
            }
        )
    return hits


def delete_document(source: str, collection: str = "") -> int:
    collection = _normalize_collection(collection)
    client = _client()
    point_ids: list[str] = []
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=["source"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get("source") == source:
                point_ids.append(str(point.id))
        if offset is None:
            break

    if not point_ids:
        return 0

    client.delete(collection_name=collection, points_selector=point_ids)
    return len(point_ids)


def persist_swag_profile(collection: str, point_id: str, profile: dict) -> None:
    """Persist a SWAG profile onto an existing Qdrant point."""
    collection = _normalize_collection(collection)
    client = _client()
    client.set_payload(
        collection_name=collection,
        payload={"swag_profile": profile},
        points=[point_id],
    )

