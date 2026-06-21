from __future__ import annotations

import re

from ingestion.config import settings

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def tokenize_for_overlap(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def term_grounded_in_text(term: str, text: str, min_ratio: float | None = None) -> bool:
    """True when enough of the term's tokens appear in the source chunk text."""
    ratio = min_ratio if min_ratio is not None else settings.swag_grounding_min_token_ratio
    term_tokens = tokenize_for_overlap(term)
    if not term_tokens or not text.strip():
        return False
    text_tokens = set(tokenize_for_overlap(text))
    hits = sum(1 for token in term_tokens if token in text_tokens)
    return hits / len(term_tokens) >= ratio


def fuzzy_term_match(query_term: str, candidate_term: str, threshold: float = 0.6) -> bool:
    """
    Conservative term overlap — short tokens require exact match; no bare substring
    on tokens shorter than swag_fuzzy_min_token_len.
    """
    q = query_term.lower().strip()
    c = candidate_term.lower().strip()
    if not q or not c:
        return False
    if q == c:
        return True

    min_len = settings.swag_fuzzy_min_token_len
    q_tokens = set(tokenize_for_overlap(q))
    c_tokens = set(tokenize_for_overlap(c))

    if len(q_tokens) == 1 and len(c_tokens) == 1:
        return q_tokens == c_tokens

    if len(q) >= min_len and len(c) >= min_len:
        if q in c or c in q:
            return True

    return jaccard_similarity(q_tokens, c_tokens) >= threshold
