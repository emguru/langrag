from __future__ import annotations

from collections import defaultdict

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from ingestion.config import settings
from ingestion.pipeline import get_embedder


def _client() -> QdrantClient:
    kwargs = {"url": settings.qdrant_url}
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return QdrantClient(**kwargs)


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
    *,
    hnsw_ef: int | None = None,
    exact: bool = False,
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

    search_params = None
    if exact or hnsw_ef is not None:
        from qdrant_client.models import SearchParams

        search_params = SearchParams(
            exact=bool(exact),
            hnsw_ef=hnsw_ef if hnsw_ef is not None else None,
        )

    response = client.query_points(
        collection_name=collection,
        query=vectors[0],
        limit=limit,
        query_filter=query_filter,
        search_params=search_params,
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
                "seed_key": payload.get("seed_key"),
            }
        )
    return hits


def _normalize_search_phrase(query: str) -> str:
    return " ".join(query.lower().split())


def phrase_match_candidates(
    query: str,
    collection: str = "",
    *,
    topic: str | None = None,
    max_hits: int | None = None,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Scroll for payload text containing the full query phrase (multi-word queries only)."""
    if not settings.rag_phrase_candidate_backfill:
        return []

    phrase = _normalize_search_phrase(query)
    if len(phrase.split()) < settings.rag_phrase_min_words:
        return []

    limit = max_hits if max_hits is not None else settings.rag_phrase_max_hits
    collection = _normalize_collection(collection)
    client = _client()
    exclude_ids = exclude_ids or set()
    matches: list[dict] = []
    offset = None

    while len(matches) < limit:
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
            point_id = str(point.id)
            if point_id in exclude_ids:
                continue
            text = str(payload.get("text") or "")
            if phrase not in text.lower():
                continue
            matches.append(
                {
                    "id": point_id,
                    "score": 0.0,
                    "text": text,
                    "source": payload.get("source", "unknown"),
                    "page": payload.get("page"),
                    "chunk_index": payload.get("chunk_index"),
                    "topic": payload.get("topic"),
                    "document_id": payload.get("document_id"),
                    "swag_profile": payload.get("swag_profile"),
                    "seed_key": payload.get("seed_key"),
                    "phrase_match": True,
                }
            )
            if len(matches) >= limit:
                break
        if offset is None:
            break

    return matches


def merge_phrase_candidates(
    vector_hits: list[dict],
    phrase_hits: list[dict],
) -> list[dict]:
    """Prepend phrase matches and boost score so SWAG/CRAG see them."""
    if not phrase_hits:
        return vector_hits

    top_score = max((float(h.get("score") or 0) for h in vector_hits), default=0.5)
    boosted = []
    for hit in phrase_hits:
        updated = dict(hit)
        updated["phrase_match"] = True
        updated["score"] = max(float(updated.get("score") or 0), top_score + 0.05)
        boosted.append(updated)

    seen = {h.get("id") for h in boosted}
    merged = list(boosted)
    for hit in vector_hits:
        if hit.get("id") in seen:
            continue
        merged.append(hit)
    return merged


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


