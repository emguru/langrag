"""Retrieval lift metrics vs baseline vector search (for Search RAG compare UI)."""

from __future__ import annotations

from ingestion.swag.rerank import compute_rank_changes

_BAD_TOP1_PREFIXES = ("# ", "![", "<html", "<!DOCTYPE")


def _hit_score(hit: dict) -> float:
    for key in ("score", "vector_score"):
        value = hit.get(key)
        if value is not None:
            return float(value)
    return 0.0


def _looks_like_title_chunk(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(_BAD_TOP1_PREFIXES):
        return True
    if stripped.startswith("# ") and len(stripped.splitlines()) <= 2:
        return True
    return False


def _top1_quality_fix_pct(baseline_hits: list[dict], augmented_hits: list[dict]) -> float:
    if not baseline_hits or not augmented_hits:
        return 0.0
    baseline_bad = _looks_like_title_chunk(str(baseline_hits[0].get("text") or ""))
    augmented_good = not _looks_like_title_chunk(str(augmented_hits[0].get("text") or ""))
    return 100.0 if baseline_bad and augmented_good else 0.0


def compute_lift_metrics(
    baseline_hits: list[dict],
    augmented_hits: list[dict],
    *,
    top_k: int,
    pipeline: str,
    extra: dict | None = None,
) -> dict:
    """Positive-oriented lift percentages vs baseline vector ranking."""
    window = max(1, min(top_k, len(baseline_hits) or top_k))
    base_top = baseline_hits[:window]
    aug_top = augmented_hits[:window]

    rank_changes = compute_rank_changes(baseline_hits, augmented_hits)
    improved = sum(1 for change in rank_changes if (change.get("rank_delta") or 0) > 0)
    rank_improved_pct = round(100.0 * improved / window, 1)

    base_score = _hit_score(base_top[0]) if base_top else 0.0
    aug_score = _hit_score(aug_top[0]) if aug_top else 0.0
    if base_score > 0 and aug_score > base_score:
        top1_score_lift_pct = round(100.0 * (aug_score - base_score) / base_score, 1)
    else:
        top1_score_lift_pct = 0.0

    top1_quality_fix_pct = _top1_quality_fix_pct(baseline_hits, augmented_hits)

    baseline_count = len(baseline_hits)
    augmented_count = len(augmented_hits)
    if baseline_count > 0 and augmented_count < baseline_count:
        noise_filtered_pct = round(
            100.0 * (baseline_count - augmented_count) / baseline_count,
            1,
        )
    else:
        noise_filtered_pct = 0.0

    relevant_kept_pct: float | None = None
    if extra and pipeline in {"crag", "swag_crag"}:
        retrieved = int(extra.get("retrieved") or 0)
        kept = int(extra.get("kept") or 0)
        if retrieved > 0:
            relevant_kept_pct = round(100.0 * kept / retrieved, 1)

    mean_swag_top3_pct: float | None = None
    if pipeline in {"swag", "swag_crag"} and aug_top:
        swag_scores = [
            float(hit.get("swag_score") or 0.0)
            for hit in augmented_hits[: min(3, len(augmented_hits))]
            if hit.get("swag_score") is not None
        ]
        if swag_scores:
            mean_swag_top3_pct = round(100.0 * sum(swag_scores) / len(swag_scores), 1)

    metrics: list[dict[str, float | str]] = []
    if rank_improved_pct > 0:
        metrics.append(
            {"label": "Higher-ranked results", "value_pct": rank_improved_pct}
        )
    if top1_score_lift_pct > 0:
        metrics.append({"label": "Top-1 score lift", "value_pct": top1_score_lift_pct})
    if top1_quality_fix_pct > 0:
        metrics.append(
            {"label": "Top-1 quality (body vs nav)", "value_pct": top1_quality_fix_pct}
        )
    if mean_swag_top3_pct is not None and mean_swag_top3_pct > 0:
        metrics.append(
            {"label": "Mean SWAG alignment (top 3)", "value_pct": mean_swag_top3_pct}
        )
    if noise_filtered_pct > 0:
        metrics.append({"label": "Noise filtered", "value_pct": noise_filtered_pct})
    if relevant_kept_pct is not None and relevant_kept_pct > 0:
        metrics.append({"label": "Relevant kept", "value_pct": relevant_kept_pct})

    return {
        "pipeline": pipeline,
        "rank_improved_pct": rank_improved_pct,
        "top1_score_lift_pct": top1_score_lift_pct,
        "top1_quality_fix_pct": top1_quality_fix_pct,
        "noise_filtered_pct": noise_filtered_pct,
        "relevant_kept_pct": relevant_kept_pct,
        "mean_swag_top3_pct": mean_swag_top3_pct,
        "positive_metrics": metrics,
        "has_positive_lift": bool(metrics),
        "baseline_count": baseline_count,
        "augmented_count": augmented_count,
    }
