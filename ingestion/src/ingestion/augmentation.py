from __future__ import annotations

from typing import Literal

from ingestion.config import settings
from ingestion.confidence import apply_vector_confidence_floor
from ingestion.crag import grade_chunks, rewrite_query, search_with_crag
from ingestion.lift_metrics import compute_lift_metrics
from ingestion.rag_store import search_documents
from ingestion.same_document_expansion import expand_same_document_siblings, prune_low_value_hits
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


def _confidence_blocked_response(
    query: str,
    *,
    collection_name: str,
    limit: int,
    mode: AugmentationMode,
    compare: bool,
    confidence: dict,
) -> dict:
    response: dict = {
        "query": query,
        "hits": [],
        "collection": collection_name,
        "mode": mode,
        "confidence": confidence,
        "swag": None,
        "crag": None,
    }
    if compare:
        response["baseline_hits"] = []
        response["comparison"] = {
            "baseline_count": 0,
            "augmented_count": 0,
            "rank_changes": [],
        }
        empty = compute_lift_metrics([], [], top_k=limit, pipeline="swag")
        response["lift"] = {
            "baseline_top_k": limit,
            "swag": {**empty, "pipeline": "swag"},
            "crag": {**empty, "pipeline": "crag"},
            "swag_crag": {**empty, "pipeline": "swag_crag"},
        }
    return response


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

    _, confidence = apply_vector_confidence_floor(baseline_hits)
    crag_can_fallback = mode in ("crag", "swag_crag") and settings.crag_enabled
    if confidence and not crag_can_fallback:
        return _confidence_blocked_response(
            query,
            collection_name=collection_name,
            limit=limit,
            mode=mode,
            compare=compare,
            confidence=confidence,
        )

    swag_meta: dict | None = None
    crag_meta: dict | None = None
    expansion_meta: dict | None = None
    hits: list[dict]
    candidate_pool = baseline_hits

    if mode == "baseline":
        if settings.rag_expand_same_document:
            candidate_pool = search_documents(
                query,
                collection=collection,
                top_k=_candidate_pool_size(limit),
                topic=topic,
            )
        hits = baseline_hits
    elif mode == "swag":
        if not settings.swag_enabled:
            hits = baseline_hits
            swag_meta = {"enabled": False, "reason": "SWAG disabled in config"}
        else:
            candidate_pool = search_documents(
                query,
                collection=collection,
                top_k=_candidate_pool_size(limit),
                topic=topic,
            )
            hits, swag_meta = rerank_hits_with_swag(
                query, candidate_pool, collection=collection, top_k=limit
            )
    elif mode == "crag":
        if not settings.crag_enabled:
            hits = baseline_hits
            crag_meta = {"enabled": False, "reason": "CRAG disabled in config"}
        else:
            if settings.rag_expand_same_document:
                candidate_pool = search_documents(
                    query,
                    collection=collection,
                    top_k=_candidate_pool_size(limit),
                    topic=topic,
                )
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
            candidate_pool = search_documents(
                query,
                collection=collection,
                top_k=_candidate_pool_size(limit),
                topic=topic,
            )
            swag_hits, swag_meta = rerank_hits_with_swag(
                query, candidate_pool, collection=collection, top_k=limit
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
            if settings.rag_expand_same_document:
                candidate_pool = search_documents(
                    query,
                    collection=collection,
                    top_k=_candidate_pool_size(limit),
                    topic=topic,
                )
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

    hits, expansion_meta = expand_same_document_siblings(
        hits, candidate_pool, top_k=limit
    )
    hits, prune_meta = prune_low_value_hits(hits, top_k=limit)
    if prune_meta.get("filtered"):
        expansion_meta = {**(expansion_meta or {}), "prune": prune_meta}

    response: dict = {
        "query": query,
        "hits": hits,
        "collection": collection_name,
        "mode": mode,
        "swag": swag_meta,
        "crag": crag_meta,
        "expansion": expansion_meta,
    }

    if compare:
        response["baseline_hits"] = baseline_hits
        response["comparison"] = {
            "baseline_count": len(baseline_hits),
            "augmented_count": len(hits),
            "rank_changes": compute_rank_changes(baseline_hits, hits),
        }

        swag_hits_lift = baseline_hits
        swag_meta_lift: dict | None = {"enabled": False, "reason": "SWAG disabled in config"}
        if settings.swag_enabled:
            swag_candidates = search_documents(
                query,
                collection=collection,
                top_k=_candidate_pool_size(limit),
                topic=topic,
            )
            swag_hits_lift, swag_meta_lift = rerank_hits_with_swag(
                query,
                swag_candidates,
                collection=collection,
                top_k=limit,
            )

        crag_hits_lift = baseline_hits
        crag_meta_lift: dict | None = {"enabled": False, "reason": "CRAG disabled in config"}
        if settings.crag_enabled:
            crag_result = search_with_crag(
                query,
                collection=collection,
                top_k=limit,
                topic=topic,
            )
            crag_hits_lift = crag_result["hits"]
            crag_meta_lift = crag_result.get("crag")

        swag_crag_hits_lift, swag_crag_meta_lift = _apply_crag_to_hits(
            query,
            swag_hits_lift,
            collection=collection,
            top_k=limit,
            topic=topic,
            swag_on_retry=True,
        )
        if not settings.crag_enabled:
            swag_crag_hits_lift = swag_hits_lift
            swag_crag_meta_lift = {"enabled": False, "reason": "CRAG disabled in config"}

        response["lift"] = {
            "baseline_top_k": limit,
            "swag": compute_lift_metrics(
                baseline_hits,
                swag_hits_lift,
                top_k=limit,
                pipeline="swag",
                extra=swag_meta_lift,
            ),
            "crag": compute_lift_metrics(
                baseline_hits,
                crag_hits_lift,
                top_k=limit,
                pipeline="crag",
                extra=crag_meta_lift,
            ),
            "swag_crag": compute_lift_metrics(
                baseline_hits,
                swag_crag_hits_lift,
                top_k=limit,
                pipeline="swag_crag",
                extra=swag_crag_meta_lift,
            ),
        }

    return response
