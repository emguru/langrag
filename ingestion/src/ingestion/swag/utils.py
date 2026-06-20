from __future__ import annotations

import re


def tokenize_for_overlap(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def fuzzy_term_match(query_term: str, candidate_term: str, threshold: float = 0.6) -> bool:
    q = query_term.lower().strip()
    c = candidate_term.lower().strip()

    if q == c:
        return True
    if q in c or c in q:
        return True

    q_tokens = set(tokenize_for_overlap(q))
    c_tokens = set(tokenize_for_overlap(c))

    if len(q_tokens) == 1 and len(c_tokens) == 1:
        return q_tokens == c_tokens

    return jaccard_similarity(q_tokens, c_tokens) >= threshold
