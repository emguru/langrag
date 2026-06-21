"""Legacy SWAG scoring (pre P2 integrity fixes) for benchmark comparison."""

from __future__ import annotations

import re

from ingestion.swag.schemas import SemanticCategory, SWAGProfile

CATEGORY_WEIGHTS: dict[SemanticCategory, float] = {
    SemanticCategory.PRIMARY_CONCEPT: 1.0,
    SemanticCategory.DOMAIN_TERM: 0.85,
    SemanticCategory.DIRECT_SYNONYM: 0.70,
    SemanticCategory.ADJACENT_CONCEPT: 0.60,
    SemanticCategory.BUSINESS_GOAL: 0.75,
    SemanticCategory.USER_INTENT: 0.90,
    SemanticCategory.NEGATIVE_CONTEXT: 0.0,
}

NEGATIVE_MATCH_PENALTY = 0.4


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


def _extract_query_terms(query_profile: SWAGProfile) -> list[tuple[str, SemanticCategory, float]]:
    if query_profile.weighted_terms:
        return [(wt.term, wt.category, wt.weight) for wt in query_profile.weighted_terms]

    terms: list[tuple[str, SemanticCategory, float]] = []
    for concept in query_profile.primary_concepts:
        terms.append((concept, SemanticCategory.PRIMARY_CONCEPT, 0.9))
    for term in query_profile.domain_terms:
        terms.append((term, SemanticCategory.DOMAIN_TERM, 0.8))
    for syn in query_profile.direct_synonyms:
        terms.append((syn, SemanticCategory.DIRECT_SYNONYM, 0.7))
    for adj in query_profile.adjacent_concepts:
        terms.append((adj, SemanticCategory.ADJACENT_CONCEPT, 0.6))
    for goal in query_profile.business_goals:
        terms.append((goal, SemanticCategory.BUSINESS_GOAL, 0.75))
    for intent in query_profile.user_intents:
        terms.append((intent, SemanticCategory.USER_INTENT, 0.85))
    return terms


def _extract_chunk_terms(chunk_profile: SWAGProfile) -> list[tuple[str, SemanticCategory, float]]:
    if chunk_profile.weighted_terms:
        return [(wt.term, wt.category, wt.weight) for wt in chunk_profile.weighted_terms]

    terms: list[tuple[str, SemanticCategory, float]] = []
    for concept in chunk_profile.primary_concepts:
        terms.append((concept, SemanticCategory.PRIMARY_CONCEPT, 0.9))
    for term in chunk_profile.domain_terms:
        terms.append((term, SemanticCategory.DOMAIN_TERM, 0.8))
    for syn in chunk_profile.direct_synonyms:
        terms.append((syn, SemanticCategory.DIRECT_SYNONYM, 0.7))
    for adj in chunk_profile.adjacent_concepts:
        terms.append((adj, SemanticCategory.ADJACENT_CONCEPT, 0.6))
    for goal in chunk_profile.business_goals:
        terms.append((goal, SemanticCategory.BUSINESS_GOAL, 0.75))
    for intent in chunk_profile.user_intents:
        terms.append((intent, SemanticCategory.USER_INTENT, 0.85))
    for neg in chunk_profile.negative_contexts:
        terms.append((neg, SemanticCategory.NEGATIVE_CONTEXT, 1.0))
    return terms


def score_swag_alignment(
    query_profile: SWAGProfile,
    chunk_profile: SWAGProfile,
    *,
    chunk_text: str = "",
) -> tuple[float, list[str]]:
    del chunk_text  # legacy ignored grounding
    if not chunk_profile or chunk_profile.confidence == 0.0:
        return 0.0, ["No SWAG profile available for chunk"]

    query_terms = _extract_query_terms(query_profile)
    chunk_terms = _extract_chunk_terms(chunk_profile)
    if not query_terms or not chunk_terms:
        return 0.0, ["Insufficient semantic terms for comparison"]

    positive_score = 0.0
    max_possible = 0.0
    explanations: list[str] = []
    negative_hits = 0

    chunk_term_map: dict[str, list[tuple[SemanticCategory, float]]] = {}
    chunk_negatives: list[str] = []

    for term, category, weight in chunk_terms:
        if category == SemanticCategory.NEGATIVE_CONTEXT:
            chunk_negatives.append(term)
        else:
            chunk_term_map.setdefault(term.lower(), []).append((category, weight))

    for q_term, q_category, q_weight in query_terms:
        cat_weight = CATEGORY_WEIGHTS.get(q_category, 0.5)
        term_max = q_weight * cat_weight
        max_possible += term_max

        best_match = 0.0
        for c_term, entries in chunk_term_map.items():
            if fuzzy_term_match(q_term, c_term):
                for c_category, c_weight in entries:
                    c_cat_weight = CATEGORY_WEIGHTS.get(c_category, 0.5)
                    match_score = q_weight * c_weight * min(cat_weight, c_cat_weight)
                    best_match = max(best_match, match_score)

        if best_match > 0:
            positive_score += best_match

        for neg_term in chunk_negatives:
            if fuzzy_term_match(q_term, neg_term):
                negative_hits += 1

    if (
        query_profile.detected_domain != "general"
        and chunk_profile.detected_domain != "general"
        and query_profile.detected_domain.lower() != chunk_profile.detected_domain.lower()
    ):
        positive_score *= 0.7

    normalized = positive_score / max_possible if max_possible > 0 else 0.0
    if negative_hits > 0:
        normalized = max(0.0, normalized - NEGATIVE_MATCH_PENALTY * negative_hits)

    return min(1.0, max(0.0, normalized)), explanations
