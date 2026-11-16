"""In-AWS retrieval smoke eval. Hits the same hybrid+rerank path as /rag/search."""

from __future__ import annotations

from ingestion.search import search_baseline

DEFAULT_QUERIES = (
    "what is retrieval augmented generation",
    "how does chunking work",
    "langgraph human in the loop",
)


def run_eval(queries: list[str] | None = None) -> dict:
    items = queries or list(DEFAULT_QUERIES)
    results = []
    for query in items:
        result = search_baseline(query, top_k=3)
        hits = result.get("hits") or []
        results.append(
            {
                "query": query,
                "hit_count": len(hits),
                "hybrid": bool(result.get("hybrid")),
                "reranked": bool(result.get("reranked")),
                "top_source": hits[0].get("source") if hits else None,
            }
        )
    passed = sum(1 for row in results if row["hit_count"] > 0)
    return {
        "passed": passed,
        "total": len(results),
        "ok": passed >= 1,
        "results": results,
    }
