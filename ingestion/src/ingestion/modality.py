"""Per-chunk modality and vector score floors.

Markdown, code, tables, and prose do not share one cosine distribution.
A single global floor either drops useful code/nav chunks or lets weak prose through.
"""

from __future__ import annotations

from ingestion.config import settings

MODALITIES = ("prose", "markdown", "code", "table")
_CODE_SUFFIXES = (
    ".py",
    ".tf",
    ".json",
    ".yaml",
    ".yml",
    ".sh",
    ".js",
    ".ts",
    ".go",
    ".rs",
)


def infer_chunk_modality(hit: dict) -> str:
    labeled = str(hit.get("modality") or "").strip().lower()
    if labeled in MODALITIES:
        return labeled
    seed = str(hit.get("seed_key") or hit.get("source") or "").lower()
    text = str(hit.get("text") or "")
    kind = str(hit.get("chunk_kind") or "").lower()
    if seed.endswith(_CODE_SUFFIXES):
        return "code"
    if "|" in text and text.count("|") >= 4 and text.count("\n") >= 2:
        return "table"
    if seed.endswith(".md") or kind in {"nav", "title", "boilerplate"}:
        return "markdown"
    return "prose"


def score_floors(*, topic: str | None = None, override: float | None = None) -> dict[str, float]:
    if override is not None:
        value = float(override)
        return {name: value for name in MODALITIES}
    floors = {
        "prose": float(settings.rag_min_vector_score_prose),
        "markdown": float(settings.rag_min_vector_score_markdown),
        "code": float(settings.rag_min_vector_score_code),
        "table": float(settings.rag_min_vector_score_table),
    }
    if topic is not None:
        cap = float(settings.rag_min_vector_score_topic)
        floors = {name: min(value, cap) for name, value in floors.items()}
    return floors


def annotate_and_keep(hits: list[dict], floors: dict[str, float]) -> list[dict]:
    kept: list[dict] = []
    default = floors.get("prose", 0.12)
    for hit in hits:
        item = dict(hit)
        modality = infer_chunk_modality(item)
        item["modality"] = modality
        floor = floors.get(modality, default)
        if float(item.get("score") or 0) >= floor:
            kept.append(item)
    return kept
