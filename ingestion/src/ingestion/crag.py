from __future__ import annotations

from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

from ingestion.config import settings
from ingestion.rag_store import merge_phrase_candidates, phrase_match_candidates, search_documents
from ingestion.swag.utils import tokenize_for_overlap


class ChunkGrade(BaseModel):
    chunk_index: int = Field(description="Zero-based index of the chunk in the input list")
    label: Literal["relevant", "ambiguous", "irrelevant"]


class RelevanceGrades(BaseModel):
    grades: list[ChunkGrade]


class RewriteQuery(BaseModel):
    query: str = Field(description="Improved search query for document retrieval")


def _get_grader_llm():
    if settings.llm_provider == "openai" and settings.openai_api_key:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=settings.crag_grader_model, temperature=0)

    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=settings.crag_grader_model,
        region_name=settings.bedrock_region,
        temperature=0,
    )


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _score_fallback_grade(hit: dict) -> str:
    score = float(hit.get("score") or 0)
    if score >= settings.crag_relevant_score:
        return "relevant"
    if score >= settings.crag_ambiguous_score:
        return "ambiguous"
    return "irrelevant"


_LEXICAL_STOPWORDS = frozenset(
    {
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "how",
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "has",
        "have",
        "having",
        "was",
        "were",
        "our",
        "out",
        "with",
        "from",
        "into",
        "about",
        "that",
        "this",
        "they",
        "them",
        "their",
        "there",
        "then",
        "than",
        "some",
        "such",
        "only",
        "also",
        "just",
        "does",
        "did",
        "doing",
        "will",
        "would",
        "could",
        "should",
        "your",
        "work",
        "show",
        "tell",
        "give",
        "find",
        "explain",
    }
)


_LEXICAL_MIN_TOKEN_LEN = 3

# Substantive query tokens mapped to fixture/batch topic families (cross-domain bait detection).
_DOMAIN_TOKEN_HINTS: dict[str, frozenset[str]] = {
    "ap-procurement": frozenset(
        {
            "invoice",
            "invoices",
            "procurement",
            "payable",
            "purchase",
            "matching",
            "vendor",
            "receipt",
            "goods",
        }
    ),
    "cafeteria-ops": frozenset(
        {
            "cafeteria",
            "menu",
            "food",
            "allergen",
            "kitchen",
            "safety",
            "grilled",
        }
    ),
    "rag-foundations": frozenset(
        {
            "rag",
            "retrieval",
            "chunking",
            "embedding",
            "augmented",
            "generation",
        }
    ),
    "adversarial": frozenset(
        {
            "pancake",
            "pancakes",
            "recipe",
            "flour",
            "buttermilk",
        }
    ),
    "retail-catalog": frozenset(
        {
            "shoe",
            "shoes",
            "sizing",
            "catalog",
            "athletic",
        }
    ),
}


def _substantive_query_tokens(query: str) -> list[str]:
    """Drop stopwords; keep short domain terms (rag, po, api) for lexical rescue."""
    tokens: list[str] = []
    for token in tokenize_for_overlap(query):
        if len(token) < _LEXICAL_MIN_TOKEN_LEN or token in _LEXICAL_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _token_fuzzy_match(query_token: str, chunk_token: str) -> bool:
    if query_token == chunk_token:
        return True
    if len(query_token) < _LEXICAL_MIN_TOKEN_LEN or len(chunk_token) < _LEXICAL_MIN_TOKEN_LEN:
        return False
    if len(query_token) < 4 or len(chunk_token) < 4:
        return False
    ratio = SequenceMatcher(None, query_token, chunk_token).ratio()
    return ratio >= settings.crag_lexical_fuzzy_ratio


def lexical_query_matches_chunk(query: str, chunk_text: str) -> bool:
    """True when a substantive query term appears in chunk text (exact or typo match)."""
    query_tokens = _substantive_query_tokens(query)
    if not query_tokens or not chunk_text.strip():
        return False
    chunk_tokens = tokenize_for_overlap(chunk_text)
    if not chunk_tokens:
        return False

    matched = 0
    for q_tok in query_tokens:
        if q_tok in chunk_tokens:
            matched += 1
            continue
        for c_tok in chunk_tokens:
            if _token_fuzzy_match(q_tok, c_tok):
                matched += 1
                break

    if matched == 0:
        return False
    if len(query_tokens) == 1:
        return matched >= 1
    # Prefer distinctive terms (cafeteria, pancackes) over a single generic overlap.
    if any(len(t) >= 5 for t in query_tokens if t in chunk_tokens or any(
        _token_fuzzy_match(t, c) for c in chunk_tokens
    )):
        return True
    return matched >= 2


def _apply_lexical_rescue(query: str, graded: list[dict]) -> list[dict]:
    """Upgrade irrelevant chunks when the grader cleared everything but text matches."""
    if not settings.crag_lexical_rescue or not graded:
        return graded
    if any(h.get("relevance") in {"relevant", "ambiguous"} for h in graded):
        return graded

    rescued: list[dict] = []
    for hit in graded:
        text = str(hit.get("text") or "")
        if lexical_query_matches_chunk(query, text):
            updated = dict(hit)
            updated["relevance"] = "ambiguous"
            updated["lexical_rescue"] = True
            rescued.append(updated)
        else:
            rescued.append(hit)
    return rescued


def grade_chunks(query: str, hits: list[dict]) -> list[dict]:
    if not hits:
        return []

    lines = []
    for idx, hit in enumerate(hits):
        lines.append(
            f"[{idx}] source={hit.get('source', 'unknown')} "
            f"score={hit.get('score', 0):.3f}\n"
            f"{_truncate(str(hit.get('text', '')), settings.crag_grade_chunk_chars)}"
        )

    prompt = (
        "Grade each retrieved chunk for answering the user query.\n"
        "- relevant: directly useful\n"
        "- ambiguous: partially related or unclear\n"
        "- irrelevant: off-topic or useless\n\n"
        f"Query: {query}\n\n"
        "Chunks:\n" + "\n\n".join(lines)
    )

    graded = [dict(hit) for hit in hits]
    try:
        llm = _get_grader_llm().with_structured_output(RelevanceGrades)
        result: RelevanceGrades = llm.invoke(prompt)
        label_by_index = {g.chunk_index: g.label for g in result.grades}
        for idx, hit in enumerate(graded):
            hit["relevance"] = label_by_index.get(idx, _score_fallback_grade(hit))
    except Exception:
        for hit in graded:
            hit["relevance"] = _score_fallback_grade(hit)

    return _apply_lexical_rescue(query, graded)


def rewrite_query(query: str) -> str:
    try:
        llm = _get_grader_llm().with_structured_output(RewriteQuery)
        result: RewriteQuery = llm.invoke(
            "Rewrite this search query to improve document retrieval. "
            "Return a concise query only.\n\n"
            f"Original: {query}"
        )
        rewritten = result.query.strip()
        return rewritten or query
    except Exception:
        return query


def _partition(hits: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    relevant = [h for h in hits if h.get("relevance") == "relevant"]
    ambiguous = [h for h in hits if h.get("relevance") == "ambiguous"]
    irrelevant = [h for h in hits if h.get("relevance") == "irrelevant"]
    return relevant, ambiguous, irrelevant


def _matched_domain_families(query: str) -> set[str]:
    tokens = set(_substantive_query_tokens(query))
    if not tokens:
        return set()
    families: set[str] = set()
    for family, hints in _DOMAIN_TOKEN_HINTS.items():
        if tokens & hints:
            families.add(family)
    return families


def _cross_domain_multi_intent_query(query: str) -> bool:
    """True when the query names two or more topic families (e.g. invoice + cafeteria)."""
    return len(_matched_domain_families(query)) >= 2


def _hit_matches_family_query_tokens(query: str, hit: dict, family: str) -> bool:
    """True when the hit text matches at least one substantive query token for this family."""
    hints = _DOMAIN_TOKEN_HINTS.get(family, frozenset())
    family_query_tokens = [
        token for token in _substantive_query_tokens(query) if token in hints
    ]
    if not family_query_tokens:
        return True
    text = str(hit.get("text") or "")
    chunk_tokens = tokenize_for_overlap(text)
    return any(
        token in chunk_tokens
        or any(_token_fuzzy_match(token, chunk_token) for chunk_token in chunk_tokens)
        for token in family_query_tokens
    )


def _hit_covers_query_domain_intents(query: str, hit: dict) -> bool:
    """Ambiguous-only keep requires the chunk to answer every domain family in the query."""
    query_families = _matched_domain_families(query)
    if len(query_families) < 2:
        return True
    return all(
        _hit_matches_family_query_tokens(query, hit, family)
        for family in query_families
    )


def _crag_lexical_keep(query: str, hit: dict) -> bool:
    if hit.get("lexical_rescue"):
        return True
    text = str(hit.get("text") or "")
    if not lexical_query_matches_chunk(query, text):
        return False
    # Ambiguous hits are already grader-approved partial matches (e.g. "tennis shoes"
    # → athletic catalog with "shoes"). lexical_query_matches_chunk is enough.
    return True


def _cross_topic_ambiguous_query(query: str, ambiguous: list[dict]) -> bool:
    """True when substantive terms land in different fixture topics (cross-domain noise)."""
    tokens = _substantive_query_tokens(query)
    if len(tokens) < 2:
        return False
    topics: set[str] = set()
    for hit in ambiguous:
        seed_key = str(hit.get("seed_key") or "")
        topic = seed_key.split("/", 1)[0] if "/" in seed_key else ""
        if not topic:
            continue
        text = str(hit.get("text") or "")
        chunk_tokens = tokenize_for_overlap(text)
        for token in tokens:
            if token in chunk_tokens or any(
                _token_fuzzy_match(token, c) for c in chunk_tokens
            ):
                topics.add(topic)
    return len(topics) >= 2


def decide_low_relevance_outcome(
    relevant: list[dict],
    ambiguous: list[dict],
    *,
    top_k: int,
    query: str = "",
) -> tuple[str, list[dict]]:
    """
    When too few chunks grade as relevant, keep partial RAG only if at least one
    relevant hit exists or lexical rescue / strong single-topic keyword match applies.
    Grader-only ambiguous hits on cross-domain queries → web_fallback.
    """
    if relevant:
        return "partial_rag", (relevant + ambiguous)[:top_k]
    if ambiguous and _cross_topic_ambiguous_query(query, ambiguous):
        return "web_fallback", []
    kept_ambiguous = [h for h in ambiguous if _crag_lexical_keep(query, h)]
    if kept_ambiguous and _cross_domain_multi_intent_query(query):
        kept_ambiguous = [
            h for h in kept_ambiguous if _hit_covers_query_domain_intents(query, h)
        ]
    if kept_ambiguous:
        return "partial_rag", kept_ambiguous[:top_k]
    if ambiguous and _cross_domain_multi_intent_query(query):
        return "web_fallback", []
    return "web_fallback", []


def _resolve_below_min_relevant(
    relevant: list[dict],
    ambiguous: list[dict],
    *,
    top_k: int,
    query: str,
) -> tuple[str, list[dict]]:
    """Keep partial RAG when the grader found some signal but not enough for use_rag."""
    if relevant:
        return "partial_rag", relevant[:top_k]
    return decide_low_relevance_outcome(
        relevant, ambiguous, top_k=top_k, query=query
    )


def hits_for_crag_grading(hits: list[dict], grade_limit: int) -> list[dict]:
    """Always send explicit phrase matches to the grader, then fill from ranked hits."""
    phrase_hits = [h for h in hits if h.get("phrase_match")]
    if not phrase_hits:
        return hits[:grade_limit]
    phrase_ids = {h.get("id") for h in phrase_hits}
    rest = [h for h in hits if h.get("id") not in phrase_ids][:grade_limit]
    return phrase_hits + rest


def search_with_crag(
    query: str,
    collection: str = "",
    top_k: int | None = None,
    topic: str | None = None,
) -> dict:
    limit = top_k or settings.rag_top_k
    retrieve_k = min(max(limit * 2, limit), settings.crag_retrieve_multiplier * limit)

    vector_hits = search_documents(query, collection=collection, top_k=retrieve_k, topic=topic)
    phrase_hits = phrase_match_candidates(
        query,
        collection=collection,
        topic=topic,
        exclude_ids={h.get("id") for h in vector_hits if h.get("id")},
    )
    hits = merge_phrase_candidates(vector_hits, phrase_hits)
    if not hits:
        return {
            "query": query,
            "hits": [],
            "collection": collection or settings.qdrant_collection,
            "crag": {
                "enabled": True,
                "action": "web_fallback",
                "retrieved": 0,
                "kept": 0,
                "rewritten_query": None,
            },
        }

    grade_limit = max(1, settings.crag_grade_max_chunks)
    graded = grade_chunks(query, hits_for_crag_grading(hits, grade_limit))
    relevant, ambiguous, irrelevant = _partition(graded)
    action = "use_rag"
    kept = relevant[:limit]
    rewritten_query: str | None = None

    min_relevant = max(1, limit // 2)
    if len(relevant) < min_relevant:
        if ambiguous:
            rewritten_query = rewrite_query(query)
            if rewritten_query.lower() != query.lower():
                retry_hits = search_documents(
                    rewritten_query,
                    collection=collection,
                    top_k=retrieve_k,
                    topic=topic,
                )
                retry_graded = grade_chunks(
                    rewritten_query, retry_hits[:grade_limit]
                )
                retry_relevant, retry_ambiguous, _ = _partition(retry_graded)
                if len(retry_relevant) >= min_relevant:
                    action = "rewritten"
                    kept = retry_relevant[:limit]
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                    irrelevant = []
                elif retry_relevant or retry_ambiguous:
                    action, kept = decide_low_relevance_outcome(
                        retry_relevant, retry_ambiguous, top_k=limit, query=rewritten_query
                    )
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                else:
                    action, kept = _resolve_below_min_relevant(
                        retry_relevant, retry_ambiguous, top_k=limit, query=rewritten_query
                    )
            else:
                action, kept = decide_low_relevance_outcome(
                    relevant, ambiguous, top_k=limit, query=query
                )
        else:
            action, kept = _resolve_below_min_relevant(
                relevant, ambiguous, top_k=limit, query=query
            )

    return {
        "query": query,
        "hits": kept,
        "collection": collection or settings.qdrant_collection,
        "crag": {
            "enabled": True,
            "action": action,
            "retrieved": len(hits),
            "kept": len(kept),
            "relevant": len(relevant),
            "ambiguous": len(ambiguous),
            "irrelevant": len(irrelevant),
            "rewritten_query": rewritten_query,
        },
    }
