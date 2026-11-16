"""Hybrid dense + lexical retrieval on Qdrant, optional Amazon Rerank 1.0."""

from __future__ import annotations

from ingestion.config import settings
from ingestion.hybrid import merge_unique, rank_by_lexical, rrf_merge
from ingestion.modality import annotate_and_keep, score_floors
from ingestion.rag_store import phrase_match_candidates, search_documents
from ingestion.rerank import amazon_rerank


def search_baseline(
    query: str,
    *,
    collection: str = "",
    top_k: int | None = None,
    topic: str | None = None,
    min_score: float | None = None,
    hnsw_ef: int | None = None,
    exact_search: bool = False,
    candidate_multiplier: int = 3,
    hybrid: bool | None = None,
    rerank: bool | None = None,
) -> dict:
    limit = top_k or settings.rag_top_k
    use_hybrid = settings.rag_hybrid if hybrid is None else bool(hybrid)
    use_rerank = settings.rag_rerank if rerank is None else bool(rerank)
    mult = max(1, min(int(candidate_multiplier or 1), 5))
    fetch_k = min(limit * mult, 50)

    dense_hits = search_documents(
        query,
        collection=collection,
        top_k=fetch_k,
        topic=topic,
        hnsw_ef=hnsw_ef,
        exact=exact_search,
    )

    floors = score_floors(topic=topic, override=min_score)
    raw_count = len(dense_hits)
    dense = annotate_and_keep(dense_hits, floors)

    phrase: list[dict] = []
    if use_hybrid:
        phrase = annotate_and_keep(
            phrase_match_candidates(
                query,
                collection=collection,
                topic=topic,
                exclude_ids={str(h.get("id")) for h in dense if h.get("id") is not None},
            ),
            {name: 0.0 for name in floors},
        )

    pool = merge_unique(dense, phrase)
    if use_hybrid and pool:
        lex_ranked = rank_by_lexical(query, pool)
        hits = rrf_merge(dense, lex_ranked)
    else:
        hits = pool

    reranked = False
    if use_rerank and hits:
        reranked_hits = amazon_rerank(query, hits, top_n=limit)
        if reranked_hits is not None:
            hits = reranked_hits
            reranked = True

    collection_name = collection or settings.qdrant_collection
    return {
        "query": query,
        "hits": hits[:limit],
        "collection": collection_name,
        "top_k": limit,
        "topic": topic,
        "min_score": min_score,
        "score_floors": floors,
        "score_floor_mode": "global_override" if min_score is not None else "per_modality",
        "hnsw_ef": hnsw_ef,
        "exact_search": exact_search,
        "candidates_fetched": raw_count,
        "candidates_after_floor": len(dense),
        "hybrid": use_hybrid,
        "reranked": reranked,
        "phrase_hits": len(phrase),
    }
