"""Vector confidence gating for off-corpus / low-similarity queries."""

from __future__ import annotations

from ingestion.config import settings


def top_vector_score(hits: list[dict]) -> float:
    if not hits:
        return 0.0
    return max(float(hit.get("score") or 0.0) for hit in hits)


def apply_vector_confidence_floor(
    hits: list[dict],
    *,
    threshold: float | None = None,
) -> tuple[list[dict], dict | None]:
    """
    Drop hits when the best vector score is below the corpus confidence floor.
    Returns ([], meta) when gated; (hits, None) when OK.
    Set threshold <= 0 to disable.
    """
    floor = settings.rag_min_vector_score if threshold is None else threshold
    if floor <= 0 or not hits:
        return hits, None

    top = top_vector_score(hits)
    if top >= floor:
        return hits, None

    return [], {
        "low_confidence": True,
        "top_vector_score": round(top, 4),
        "min_vector_score": floor,
        "message": (
            "No confident matches in this collection for that query. "
            "Try a topic covered by your indexed documents, or use CRAG for web fallback."
        ),
    }
