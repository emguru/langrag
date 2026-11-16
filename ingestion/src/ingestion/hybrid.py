"""Lexical + dense fusion on Qdrant hits. No OpenSearch."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "as",
        "at",
        "by",
        "from",
        "with",
        "that",
        "this",
        "it",
        "its",
        "how",
        "what",
        "when",
        "where",
        "why",
        "does",
        "do",
        "did",
    }
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def lexical_score(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    doc_tokens = set(tokenize(text))
    return sum(1 for token in query_tokens if token in doc_tokens) / len(query_tokens)


def rank_by_lexical(query: str, hits: list[dict]) -> list[dict]:
    scored: list[dict] = []
    for hit in hits:
        item = dict(hit)
        item["lexical_score"] = lexical_score(query, str(hit.get("text") or ""))
        scored.append(item)
    scored.sort(key=lambda h: float(h.get("lexical_score") or 0), reverse=True)
    return scored


def rrf_merge(*ranked_lists: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal rank fusion over one or more ranked hit lists."""
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked):
            hid = str(hit.get("id") or f"anon-{id(hit)}")
            scores[hid] = scores.get(hid, 0.0) + 1.0 / (k + rank + 1)
            existing = by_id.get(hid)
            if existing is None:
                by_id[hid] = dict(hit)
            else:
                merged = dict(existing)
                for key, value in hit.items():
                    if value is not None:
                        merged[key] = value
                by_id[hid] = merged
    ordered = sorted(scores, key=lambda hid: scores[hid], reverse=True)
    out: list[dict] = []
    for hid in ordered:
        item = dict(by_id[hid])
        item["rrf_score"] = scores[hid]
        out.append(item)
    return out


def merge_unique(*lists: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for hits in lists:
        for hit in hits:
            hid = str(hit.get("id") or "")
            key = hid or f"{hit.get('source')}:{hit.get('chunk_index')}:{id(hit)}"
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(hit))
    return out
