from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ingestion.config import settings
from ingestion.rag_store import search_documents


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

    return graded


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


def search_with_crag(
    query: str,
    collection: str = "",
    top_k: int | None = None,
    topic: str | None = None,
) -> dict:
    limit = top_k or settings.rag_top_k
    retrieve_k = min(max(limit * 2, limit), settings.crag_retrieve_multiplier * limit)

    hits = search_documents(query, collection=collection, top_k=retrieve_k, topic=topic)
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

    graded = grade_chunks(query, hits)
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
                retry_graded = grade_chunks(rewritten_query, retry_hits)
                retry_relevant, retry_ambiguous, _ = _partition(retry_graded)
                if len(retry_relevant) >= min_relevant:
                    action = "rewritten"
                    kept = retry_relevant[:limit]
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                    irrelevant = []
                elif retry_relevant or retry_ambiguous:
                    action = "partial_rag"
                    kept = (retry_relevant + retry_ambiguous)[:limit]
                    relevant = retry_relevant
                    ambiguous = retry_ambiguous
                else:
                    action = "web_fallback"
                    kept = []
            else:
                action = "partial_rag" if (relevant or ambiguous) else "web_fallback"
                kept = (relevant + ambiguous)[:limit] if action == "partial_rag" else []
        else:
            action = "web_fallback"
            kept = []

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
