"""Confidence-gated SWAG rerank — skip when baseline vector search is already decisive."""

from __future__ import annotations

from ingestion.config import settings
from ingestion.swag.chunk_quality import chunk_kind_from_profile, classify_chunk_kind
from ingestion.swag.profile import profile_from_payload


def should_rerank_with_swag(hits: list[dict], *, query: str) -> tuple[bool, str]:
    del query
    if not hits:
        return False, "no_candidates"
    if len(hits) == 1:
        kind = _top_kind(hits[0])
        if kind in {"nav", "title", "boilerplate"}:
            return True, f"single_candidate_kind={kind}"
        return False, "single_body_candidate"

    top_score = float(hits[0].get("score") or 0.0)
    second_score = float(hits[1].get("score") or 0.0)
    margin = top_score - second_score
    epsilon = settings.swag_adaptive_vector_epsilon
    top_kind = _top_kind(hits[0])

    if top_kind in {"nav", "title", "boilerplate"}:
        return True, f"top1_kind={top_kind}"

    if margin <= epsilon:
        return True, "vector_margin_ambiguous"

    if top_kind == "body" and margin > epsilon:
        return False, "clear_body_winner"

    return True, "default_rerank"


def _top_kind(hit: dict) -> str:
    profile = profile_from_payload(hit.get("swag_profile"))
    text = str(hit.get("text") or "")
    if hit.get("chunk_kind"):
        return str(hit["chunk_kind"])
    return chunk_kind_from_profile(profile, text=text) or classify_chunk_kind(text)
