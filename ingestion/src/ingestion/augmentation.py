from __future__ import annotations

from typing import Literal

from ingestion.config import settings
from ingestion.crag import grade_chunks, rewrite_query, search_with_crag
from ingestion.rag_store import search_documents
from ingestion.swag.rerank import compute_rank_changes, rerank_hits_with_swag

AugmentationMode = Literal["baseline", "swag", "crag", "swag_crag"]


def _partition(hits: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    relevant = [h for h in hits if h.get("relevance") == "relevant"]
    ambiguous = [h for h in hits if h.get("relevance") == "ambiguous"]
    irrelevant = [h for h in hits if h.get("relevance") == "irrelevant"]
    return relevant, ambiguous, irrelevant


def _apply_crag_to_hits(
    query: str,
    hits: list[dict],
    *,
    collection: str,
    top_k: int,
    topic: str | None,
    swag_on_retry: bool = False,
) -> tuple[list[dict], dict]:
    if not hits:
        return [], {
            "enabled": True,
            "action": "web_fallback",
            "retrieved": 0,
            "kept": 0,
            "rewritten_query": None,
        }

    graded = grade_chunks(query, hits)
    relevant, ambiguous, irrelevant = _partition(graded)
    action = "use_rag"
    kept = relevant[:top_k]
    rewritten_query: str | None = None
    min_relevant = max(1, top_k // 2)

    if len(relevant) < min_relevant:
        if ambiguous:
            rewritten_query = rewrite_query(query)
            if rewritten_query.lower() != query.lower():
                retrieve_k = min(
                    max(top_k * 2, top_k),
                    settings.crag_retrieve_multiplier * top_k,
                )
                retry_hits = search_documents(
                    rewritten_query,
                    collection=collection,
                    top_k=retrieve_k,
                    topic=topic,
                )
                if swag_on_retry and settings.swag_enabled:
                    retry_hits, _ = rerank_hits_with_swag(
                        rewritten_query,
                        retry_hits,
                        collection=collection,
                        top_k=top_k,
                    )
                retry_graded = grade_chunks(rewritten_query, retry_hits)
                retry_relevant, retry_ambiguous, _ = _partition(retry_graded)
                if len(retry_relevant) >= min_relevant:
                    action = "rewritten"
                    kept = retry_relevant[:top_k]
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                    irrelevant = []
                elif retry_relevant or retry_ambiguous:
                    action = "partial_rag"
                    kept = (retry_relevant + retry_ambiguous)[:top_k]
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                else:
                    action = "web_fallback"
                    kept = []
            else:
                action = "partial_rag" if (relevant or ambiguous) else "web_fallback"
                kept = (relevant + ambiguous)[:top_k] if action == "partial_rag" else []
        else:
            action = "web_fallback"
            kept = []

    return kept, {
        "enabled": True,
        "action": action,
        "retrieved": len(hits),
        "kept": len(kept),
        "relevant": len(relevant),
        "ambiguous": len(ambiguous),
        "irrelevant": len(irrelevant),
        "rewritten_query": rewritten_query,
    }


def _candidate_pool_size(top_k: int) -> int:
    return max(
        top_k,
        settings.swag_retrieval_candidates,
        settings.crag_retrieve_multiplier * top_k,
    )


def search_with_augmentation(
    query: str,
    *,
    collection: str = "",
    top_k: int | None = None,
    topic: str | None = None,
    mode: AugmentationMode = "baseline",
    compare: bool = False,
) -> dict:
    limit = top_k or settings.rag_top_k
    collection_name = collection or settings.qdrant_collection

    baseline_hits = search_documents(
        query,
        collection=collection,
        top_k=limit,
        topic=topic,
    )

    swag_meta: dict | None = None
    crag_meta: dict | None = None
    hits: list[dict]

    if mode == "baseline":
        hits = baseline_hits
    elif mode == "swag":
        if not settings.swag_enabled:
            hits = baseline_hits
            swag_meta = {"enabled": False, "reason": "SWAG disabled in config"}
        else:
            candidates = search_documents(
                query,
                collection=collection,
                top_k=_candidate_pool_size(limit),
                topic=topic,
            )
            hits, swag_meta = rerank_hits_with_swag(
                query, candidates, collection=collection, top_k=limit
            )
    elif mode == "crag":
        if not settings.crag_enabled:
            hits = baseline_hits
            crag_meta = {"enabled": False, "reason": "CRAG disabled in config"}
        else:
            result = search_with_crag(
                query,
                collection=collection,
                top_k=limit,
                topic=topic,
            )
            hits = result["hits"]
            crag_meta = result.get("crag")
    elif mode == "swag_crag":
        swag_meta = {"enabled": settings.swag_enabled}
        crag_meta = {"enabled": settings.crag_enabled}
        if not settings.swag_enabled and not settings.crag_enabled:
            hits = baseline_hits
        elif settings.swag_enabled:
            candidates = search_documents(
                query,
                collection=collection,
                top_k=_candidate_pool_size(limit),
                topic=topic,
            )
            swag_hits, swag_meta = rerank_hits_with_swag(
                query, candidates, collection=collection, top_k=limit
            )
            if settings.crag_enabled:
                hits, crag_meta = _apply_crag_to_hits(
                    query,
                    swag_hits,
                    collection=collection,
                    top_k=limit,
                    topic=topic,
                    swag_on_retry=True,
                )
            else:
                hits = swag_hits
        else:
            result = search_with_crag(
                query,
                collection=collection,
                top_k=limit,
                topic=topic,
            )
            hits = result["hits"]
            crag_meta = result.get("crag")
    else:
        hits = baseline_hits

    response: dict = {
        "query": query,
        "hits": hits,
        "collection": collection_name,
        "mode": mode,
        "swag": swag_meta,
        "crag": crag_meta,
    }

    if compare:
        response["baseline_hits"] = baseline_hits
        response["comparison"] = {
            "baseline_count": len(baseline_hits),
            "augmented_count": len(hits),
            "rank_changes": compute_rank_changes(baseline_hits, hits),
        }

    return response
