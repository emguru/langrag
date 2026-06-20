from __future__ import annotations

from ingestion.config import settings
from ingestion.swag.profiles import ensure_swag_profiles
from ingestion.swag.profile import SWAGProfileGenerator, profile_from_payload
from ingestion.swag.schemas import QueryProfile
from ingestion.swag.scoring import (
    build_selection_explanation,
    compute_final_score,
    compute_metadata_boost,
    score_swag_alignment,
)


def _hit_key(hit: dict) -> str:
    return str(
        hit.get("id")
        or hit.get("document_id")
        or f"{hit.get('source', '')}:{hit.get('page', '')}:{hit.get('chunk_index', '')}"
    )


def rerank_hits_with_swag(
    query: str,
    hits: list[dict],
    *,
    collection: str = "",
    top_k: int | None = None,
) -> tuple[list[dict], dict]:
    """Rerank vector hits using SWAG semantic profile alignment."""
    limit = top_k or settings.rag_top_k
    if not hits:
        return [], {
            "enabled": True,
            "candidates": 0,
            "profiles_available": 0,
            "profiles_generated": 0,
            "profiles_cached": 0,
            "profiling_ran": False,
            "query_domain": None,
        }

    hits, profile_meta = ensure_swag_profiles(hits, collection=collection)

    generator = SWAGProfileGenerator()
    query_profile = generator.generate_query_profile(query)
    query_profile_obj = QueryProfile(original_query=query, swag_profile=query_profile)

    scored: list[dict] = []
    profiles_available = 0

    for hit in hits:
        vector_score = float(hit.get("score") or 0.0)
        chunk_profile = profile_from_payload(hit.get("swag_profile"))
        if chunk_profile and chunk_profile.confidence > 0:
            profiles_available += 1

        if chunk_profile and chunk_profile.confidence > 0:
            swag_score, swag_explanations = score_swag_alignment(
                query_profile_obj.swag_profile,
                chunk_profile,
            )
            metadata_boost = compute_metadata_boost(
                chunk_profile=chunk_profile,
                query_profile=query_profile_obj.swag_profile,
                source=str(hit.get("source") or ""),
                original_query=query,
            )
        else:
            swag_score = 0.0
            swag_explanations = ["Semantic profile unavailable for chunk"]
            metadata_boost = 0.0

        final_score = compute_final_score(
            vector_score,
            swag_score,
            metadata_boost,
            vector_weight=settings.swag_vector_weight,
            swag_weight=settings.swag_weight,
            metadata_weight=settings.swag_metadata_weight,
        )
        explanation = build_selection_explanation(
            vector_score,
            swag_score,
            metadata_boost,
            final_score,
            swag_explanations,
        )

        enriched = dict(hit)
        enriched["vector_score"] = vector_score
        enriched["swag_score"] = swag_score
        enriched["metadata_boost"] = metadata_boost
        enriched["score"] = final_score
        enriched["swag_explanation"] = explanation
        scored.append(enriched)

    scored.sort(key=lambda item: item["score"], reverse=True)
    reranked = scored[:limit]

    for rank, hit in enumerate(reranked, start=1):
        hit["swag_rank"] = rank

    meta = {
        "enabled": True,
        "candidates": len(hits),
        "profiles_available": profiles_available,
        "profiles_generated": profile_meta.get("profiles_generated", 0),
        "profiles_cached": profile_meta.get("profiles_cached", 0),
        "profiling_ran": profile_meta.get("profiling_ran", False),
        "query_domain": query_profile.detected_domain,
        "vector_weight": settings.swag_vector_weight,
        "swag_weight": settings.swag_weight,
        "metadata_weight": settings.swag_metadata_weight,
    }
    return reranked, meta


def compute_rank_changes(baseline_hits: list[dict], augmented_hits: list[dict]) -> list[dict]:
    baseline_rank = {_hit_key(hit): idx + 1 for idx, hit in enumerate(baseline_hits)}
    augmented_rank = {_hit_key(hit): idx + 1 for idx, hit in enumerate(augmented_hits)}

    changes: list[dict] = []
    for key in set(baseline_rank) | set(augmented_rank):
        before = baseline_rank.get(key)
        after = augmented_rank.get(key)
        delta = None
        if before is not None and after is not None:
            delta = before - after
        changes.append(
            {
                "key": key,
                "baseline_rank": before,
                "augmented_rank": after,
                "rank_delta": delta,
            }
        )

    changes.sort(
        key=lambda item: (
            item["augmented_rank"] if item["augmented_rank"] is not None else 999,
            item["baseline_rank"] if item["baseline_rank"] is not None else 999,
        )
    )
    return changes
