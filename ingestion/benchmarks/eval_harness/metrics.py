"""Retrieval metrics for the SWAG evaluation harness."""

from __future__ import annotations

import math
import re
from typing import Any

from validation_eval import hit_is_relevant, hit_seed_key, mrr, precision_at_k


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _text_contains_any(text: str, needles: list[str]) -> bool:
    lowered = _norm(text)
    return any(_norm(needle) in lowered for needle in needles)


def recall_at_k(hits: list[dict], relevant_seed_keys: list[str], k: int) -> float:
    if not relevant_seed_keys or k <= 0:
        return 0.0
    window = hits[:k]
    if not window:
        return 0.0
    found = {key for hit in window if (key := hit_seed_key(hit)) and hit_is_relevant(hit, relevant_seed_keys)}
    return len(found) / len(set(relevant_seed_keys))


def ndcg_at_k(hits: list[dict], relevant_seed_keys: list[str], k: int) -> float:
    if not relevant_seed_keys or k <= 0:
        return 0.0
    dcg = 0.0
    for idx, hit in enumerate(hits[:k], start=1):
        if hit_is_relevant(hit, relevant_seed_keys):
            dcg += 1.0 / math.log2(idx + 1)
    ideal_hits = min(len(set(relevant_seed_keys)), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg <= 0:
        return 0.0
    return dcg / idcg


def citation_correct_at_1(hits: list[dict], case: dict[str, Any]) -> float:
    """Top hit cites gold document and optional gold chunk marker."""
    if not hits:
        return 0.0
    top = hits[0]
    gold_doc = case.get("gold_source_document")
    relevant = list(case.get("relevant_seed_keys") or [])
    if gold_doc:
        relevant = list({*relevant, gold_doc})
    if not hit_is_relevant(top, relevant):
        return 0.0
    chunk_markers = list(case.get("gold_source_chunk_contains") or [])
    if chunk_markers and not _text_contains_any(str(top.get("text") or ""), chunk_markers):
        return 0.0
    return 1.0


def wrong_version_rate(hits: list[dict], case: dict[str, Any], *, k: int = 5) -> float:
    traps = list(case.get("trap_documents") or [])
    forbidden = list(case.get("forbidden_top1_seed_keys") or [])
    trap_keys = set(traps) | set(forbidden)
    if not trap_keys:
        return 0.0
    window = hits[:k]
    if not window:
        return 0.0
    wrong = sum(1 for hit in window if (key := hit_seed_key(hit)) and key in trap_keys)
    return wrong / len(window)


def score_retrieval(hits: list[dict], case: dict[str, Any], *, k: int = 5) -> dict[str, float]:
    relevant = list(case.get("relevant_seed_keys") or [])
    return {
        "recall_at_k": recall_at_k(hits, relevant, k),
        "precision_at_k": precision_at_k(hits, relevant, k),
        "mrr": mrr(hits, relevant),
        "ndcg_at_k": ndcg_at_k(hits, relevant, k),
        "citation_correct_at_1": citation_correct_at_1(hits, case),
        "wrong_version_rate": wrong_version_rate(hits, case, k=k),
    }


def aggregate_metric_rows(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    keys = rows[0].keys()
    return {key: sum(row.get(key, 0.0) for row in rows) / len(rows) for key in keys}
