from __future__ import annotations

from typing import Literal

from ingestion.config import settings
from ingestion.confidence import apply_vector_confidence_floor
from ingestion.crag import (
    decide_low_relevance_outcome,
    grade_chunks,
    rewrite_query,
    search_with_crag,
)
from ingestion.lift_metrics import compute_lift_metrics
from ingestion.rag_store import search_documents
from ingestion.same_document_expansion import (
    backfill_body_from_pool,
    expand_same_document_siblings,
    prune_low_value_hits,
)
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
                    action, kept = decide_low_relevance_outcome(
                        retry_relevant, retry_ambiguous, top_k=top_k, query=rewritten_query
                    )
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                else:
                    action = "web_fallback"
                    kept = []
            else:
                action, kept = decide_low_relevance_outcome(
                    relevant, ambiguous, top_k=top_k, query=query
                )
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


def _needs_wide_pool() -> bool:
    return (
        settings.rag_expand_same_document
        or settings.rag_filter_non_body_hits
        or settings.swag_enabled
    )


def _fetch_candidate_pool(
    query: str,
    *,
    collection: str,
    top_k: int,
    topic: str | None,
) -> list[dict]:
    return search_documents(
        query,
        collection=collection,
        top_k=_candidate_pool_size(top_k),
        topic=topic,
    )


def _finalize_retrieval_hits(
    hits: list[dict],
    candidate_pool: list[dict],
    *,
    top_k: int,
    allow_pool_backfill: bool = True,
    allow_sibling_expand: bool = True,
) -> tuple[list[dict], dict]:
    """Same-document expansion + body prune; optional pool backfill to reach top_k."""
    if allow_sibling_expand:
        expanded, expansion_meta = expand_same_document_siblings(
            hits, candidate_pool, top_k=top_k
        )
    else:
        expanded = hits[:top_k]
        expansion_meta = {
            "enabled": False,
            "skipped": "sibling_expand_disabled",
            "siblings_added": 0,
            "reason": "CRAG kept no relevant hits; avoid same-doc flood",
        }
    pruned, prune_meta = prune_low_value_hits(expanded, top_k=top_k)
    if prune_meta.get("filtered"):
        expansion_meta = {**(expansion_meta or {}), "prune": prune_meta}
    if allow_pool_backfill:
        backfilled, backfill_meta = backfill_body_from_pool(
            pruned, candidate_pool, top_k=top_k
        )
        if backfill_meta.get("backfilled"):
            expansion_meta = {**(expansion_meta or {}), "backfill": backfill_meta}
        return backfilled, expansion_meta
    expansion_meta = {
        **(expansion_meta or {}),
        "backfill": {"enabled": False, "backfilled": 0, "skipped": "crag_kept_hits"},
    }
    return pruned[:top_k], expansion_meta


def _symmetric_baseline_hits(
    raw_baseline: list[dict],
    candidate_pool: list[dict],
    *,
    top_k: int,
) -> list[dict]:
    """Baseline vector hits after the same expand+prune stack as augmented results."""
    if not settings.rag_compare_symmetric_baseline:
        return raw_baseline
    if not settings.rag_expand_same_document and not settings.rag_filter_non_body_hits:
        return raw_baseline
    finalized, _ = _finalize_retrieval_hits(raw_baseline, candidate_pool, top_k=top_k)
    return finalized


def _crag_cleared_all_hits(crag_meta: dict | None) -> bool:
    """CRAG returned no chunks — do not refill from the vector candidate pool."""
    if not crag_meta or crag_meta.get("enabled") is False:
        return False
    kept = int(crag_meta.get("kept") or 0)
    return kept == 0 and str(crag_meta.get("action") or "") == "web_fallback"


def _crag_kept_hits(crag_meta: dict | None) -> bool:
    """CRAG kept at least one chunk — allow sibling expand only, no cross-doc pool backfill."""
    if not crag_meta or crag_meta.get("enabled") is False:
        return False
    return int(crag_meta.get("kept") or 0) > 0


def _crag_skip_sibling_expand(crag_meta: dict | None) -> bool:
    """When CRAG kept only ambiguous hits, same-doc siblings often re-add noise."""
    if not _crag_kept_hits(crag_meta):
        return False
    return int(crag_meta.get("relevant") or 0) == 0


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
        response["baseline_hits_raw"] = []
        response["comparison"] = {
            "baseline_count": 0,
            "augmented_count": 0,
            "rank_changes": [],
        }
        empty = compute_lift_metrics([], [], top_k=limit, pipeline="swag_rerank")
        response["lift"] = {
            "baseline_top_k": limit,
            "baseline_compare": "symmetric_stack",
            "swag_rerank": {**empty, "pipeline": "swag_rerank"},
            "swag_full": {**empty, "pipeline": "swag_full"},
            "swag": {**empty, "pipeline": "swag_rerank"},
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

    _, confidence = apply_vector_confidence_floor(baseline_hits, topic=topic)
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

    raw_baseline_hits = baseline_hits
    candidate_pool = (
        _fetch_candidate_pool(query, collection=collection, top_k=limit, topic=topic)
        if _needs_wide_pool()
        else baseline_hits
    )

    swag_meta: dict | None = None
    crag_meta: dict | None = None
    expansion_meta: dict | None = None
    hits: list[dict]

    if mode == "baseline":
        hits = raw_baseline_hits
    elif mode == "swag":
        if not settings.swag_enabled:
            hits = raw_baseline_hits
            swag_meta = {"enabled": False, "reason": "SWAG disabled in config"}
        else:
            hits, swag_meta = rerank_hits_with_swag(
                query, candidate_pool, collection=collection, top_k=limit
            )
    elif mode == "crag":
        if not settings.crag_enabled:
            hits = raw_baseline_hits
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
            hits = raw_baseline_hits
        elif settings.swag_enabled:
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
            result = search_with_crag(
                query,
                collection=collection,
                top_k=limit,
                topic=topic,
            )
            hits = result["hits"]
            crag_meta = result.get("crag")
    else:
        hits = raw_baseline_hits

    if _crag_cleared_all_hits(crag_meta):
        expansion_meta = {
            "enabled": False,
            "skipped": "crag_web_fallback",
            "reason": "CRAG dropped all chunks; not backfilling from vector pool",
        }
    else:
        hits, expansion_meta = _finalize_retrieval_hits(
            hits,
            candidate_pool,
            top_k=limit,
            allow_pool_backfill=not _crag_kept_hits(crag_meta),
            allow_sibling_expand=not _crag_skip_sibling_expand(crag_meta),
        )

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
        symmetric_baseline = _symmetric_baseline_hits(
            raw_baseline_hits, candidate_pool, top_k=limit
        )
        response["baseline_hits_raw"] = raw_baseline_hits
        response["baseline_hits"] = symmetric_baseline
        response["comparison"] = {
            "baseline_count": len(symmetric_baseline),
            "baseline_raw_count": len(raw_baseline_hits),
            "augmented_count": len(hits),
            "rank_changes": compute_rank_changes(raw_baseline_hits, hits),
            "rank_changes_symmetric": compute_rank_changes(symmetric_baseline, hits),
        }

        # Only compute lift (and CRAG grading) for pipelines in the active search mode.
        # The eval sheet still requests mode=swag + compare for swag_rerank/swag_full only.
        need_swag_lift = mode in ("swag", "swag_crag")
        need_crag_lift = mode == "crag"
        need_swag_crag_lift = mode == "swag_crag"

        compare_mode = (
            "symmetric_stack"
            if settings.rag_compare_symmetric_baseline
            else "raw_vector"
        )
        lift_payload: dict = {
            "baseline_top_k": limit,
            "baseline_compare": compare_mode,
            "active_mode": mode,
        }

        if need_swag_lift or need_swag_crag_lift:
            swag_rerank_hits = raw_baseline_hits
            swag_meta_lift: dict | None = {
                "enabled": False,
                "reason": "SWAG disabled in config",
            }
            if settings.swag_enabled:
                swag_rerank_hits, swag_meta_lift = rerank_hits_with_swag(
                    query,
                    candidate_pool,
                    collection=collection,
                    top_k=limit,
                )
            swag_full_hits, _ = _finalize_retrieval_hits(
                swag_rerank_hits, candidate_pool, top_k=limit
            )
            if need_swag_lift or need_swag_crag_lift:
                swag_rerank_lift = compute_lift_metrics(
                    raw_baseline_hits,
                    swag_rerank_hits,
                    top_k=limit,
                    pipeline="swag_rerank",
                    extra=swag_meta_lift,
                )
                swag_full_lift = compute_lift_metrics(
                    symmetric_baseline,
                    swag_full_hits,
                    top_k=limit,
                    pipeline="swag_full",
                    extra=swag_meta_lift,
                )
                lift_payload["swag_rerank"] = swag_rerank_lift
                lift_payload["swag_full"] = swag_full_lift
                lift_payload["swag"] = swag_rerank_lift

            if need_swag_crag_lift:
                swag_crag_hits_lift, swag_crag_meta_lift = _apply_crag_to_hits(
                    query,
                    swag_rerank_hits,
                    collection=collection,
                    top_k=limit,
                    topic=topic,
                    swag_on_retry=True,
                )
                if not settings.crag_enabled:
                    swag_crag_hits_lift = swag_full_hits
                    swag_crag_meta_lift = {
                        "enabled": False,
                        "reason": "CRAG disabled in config",
                    }
                elif _crag_cleared_all_hits(swag_crag_meta_lift):
                    swag_crag_hits_lift = []
                else:
                    swag_crag_hits_lift, _ = _finalize_retrieval_hits(
                        swag_crag_hits_lift,
                        candidate_pool,
                        top_k=limit,
                        allow_pool_backfill=not _crag_kept_hits(swag_crag_meta_lift),
                        allow_sibling_expand=not _crag_skip_sibling_expand(
                            swag_crag_meta_lift
                        ),
                    )
                lift_payload["swag_crag"] = compute_lift_metrics(
                    symmetric_baseline,
                    swag_crag_hits_lift,
                    top_k=limit,
                    pipeline="swag_crag",
                    extra=swag_crag_meta_lift,
                )

        if need_crag_lift:
            crag_hits_lift = raw_baseline_hits
            crag_meta_lift: dict | None = {
                "enabled": False,
                "reason": "CRAG disabled in config",
            }
            if settings.crag_enabled:
                crag_result = search_with_crag(
                    query,
                    collection=collection,
                    top_k=limit,
                    topic=topic,
                )
                crag_hits_lift = crag_result["hits"]
                crag_meta_lift = crag_result.get("crag")
                if _crag_cleared_all_hits(crag_meta_lift):
                    crag_hits_lift = []
                elif crag_hits_lift:
                    crag_hits_lift, _ = _finalize_retrieval_hits(
                        crag_hits_lift,
                        candidate_pool,
                        top_k=limit,
                        allow_pool_backfill=not _crag_kept_hits(crag_meta_lift),
                        allow_sibling_expand=not _crag_skip_sibling_expand(
                            crag_meta_lift
                        ),
                    )
            lift_payload["crag"] = compute_lift_metrics(
                raw_baseline_hits,
                crag_hits_lift,
                top_k=limit,
                pipeline="crag",
                extra=crag_meta_lift,
            )

        response["lift"] = lift_payload

    return response
