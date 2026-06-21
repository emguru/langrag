"""Benchmark a live research thread against all RAG modes on AWS."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def _extract_rag_queries(history: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for item in history:
        checkpoint = item.get("checkpoint") or {}
        ch_state = checkpoint.get("channel_values") or checkpoint.get("values") or {}
        for msg in ch_state.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            for tc in msg.get("tool_calls") or []:
                if tc.get("name") != "rag_search":
                    continue
                query = (tc.get("args") or {}).get("query")
                if query and query not in seen:
                    seen.add(query)
                    queries.append(query)
    return queries


def _fetch_thread_values(thread_id: str, langgraph_url: str) -> dict[str, Any]:
    import httpx

    try:
        state_resp = httpx.get(f"{langgraph_url}/threads/{thread_id}/state", timeout=30)
        if state_resp.status_code == 200:
            return state_resp.json().get("values", {})
    except Exception:
        pass

    search_resp = httpx.post(
        f"{langgraph_url}/threads/search",
        json={"limit": 200},
        timeout=30,
    )
    search_resp.raise_for_status()
    for thread in search_resp.json():
        if thread.get("thread_id") == thread_id:
            return thread.get("values") or {}
    raise ValueError(f"Thread not found: {thread_id}")


def benchmark_thread(
    thread_id: str,
    *,
    langgraph_url: str,
    ingestion_url: str,
    collection: str = "research_docs",
    top_k: int = 8,
) -> dict[str, Any]:
    import httpx

    values = _fetch_thread_values(thread_id, langgraph_url)

    history: list[dict[str, Any]] = []
    try:
        hist_resp = httpx.get(f"{langgraph_url}/threads/{thread_id}/history", timeout=60)
        if hist_resp.status_code == 200:
            history = hist_resp.json() if isinstance(hist_resp.json(), list) else []
    except Exception:
        pass

    tool_queries = _extract_rag_queries(history)
    sub_questions = values.get("sub_questions") or []
    findings_by = {f.get("sub_question_id"): f for f in values.get("findings") or []}

    bench_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in tool_queries:
        if q not in seen:
            seen.add(q)
            bench_items.append({"source": "tool_call", "query": q})
    for sq in sub_questions:
        q = sq.get("question")
        if q and q not in seen:
            seen.add(q)
            bench_items.append(
                {"source": "sub_question", "sub_question_id": sq.get("id"), "query": q}
            )

    modes = ["baseline", "swag", "crag", "swag_crag"]
    query_results: list[dict[str, Any]] = []

    for item in bench_items:
        query = item["query"]
        row: dict[str, Any] = {
            "query": query,
            "source": item["source"],
            "modes": {},
        }
        if item.get("sub_question_id"):
            row["sub_question_id"] = item["sub_question_id"]

        for mode in modes:
            resp = httpx.post(
                f"{ingestion_url}/rag/search",
                json={
                    "query": query,
                    "collection": collection,
                    "top_k": top_k,
                    "mode": mode,
                    "compare": True,
                },
                timeout=180,
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits") or []
            baseline = data.get("baseline_hits") or []
            comparison = data.get("comparison") or {}
            rank_changes = [
                c
                for c in comparison.get("rank_changes", [])
                if c.get("rank_delta") not in (None, 0)
            ]
            row["modes"][mode] = {
                "indexed_hits": len(hits),
                "baseline_hits": len(baseline),
                "top_score": hits[0].get("score") if hits else None,
                "crag_action": (data.get("crag") or {}).get("action"),
                "swag_profiles": (data.get("swag") or {}).get("profiles_available"),
                "swag_candidates": (data.get("swag") or {}).get("candidates"),
                "rank_changes": len(rank_changes),
            }

        swag_crag = row["modes"]["swag_crag"]
        agent_replay: dict[str, Any] = {
            "indexed_hits": swag_crag["indexed_hits"],
            "crag_action": swag_crag["crag_action"],
            "web_hits": 0,
            "total_hits": swag_crag["indexed_hits"],
        }
        if swag_crag["crag_action"] == "web_fallback" or swag_crag["indexed_hits"] == 0:
            try:
                from research_team.tools import _format_web_hits, _web_search_results

                web_hits = _format_web_hits(_web_search_results(query))
                agent_replay["web_hits"] = len(web_hits)
                agent_replay["total_hits"] = len(web_hits)
                agent_replay["crag_action"] = (
                    "web_fallback+tavily" if web_hits else "web_fallback (tavily empty)"
                )
            except Exception as exc:
                agent_replay["web_error"] = str(exc)
        row["agent_replay"] = agent_replay

        sid = item.get("sub_question_id")
        if sid and sid in findings_by:
            research_hits = findings_by[sid].get("rag_hits") or []
            web = sum(
                1
                for h in research_hits
                if h.get("origin") == "web"
                or (
                    h.get("score") in (None, 0)
                    and str(h.get("source", "")).startswith("http")
                )
            )
            row["research_actual"] = {
                "total_hits": len(research_hits),
                "web_hits": web,
                "indexed_hits": len(research_hits) - web,
            }

        query_results.append(row)

    qdrant_points: int | None = None
    try:
        from qdrant_client import QdrantClient

        qdrant_url = os.environ.get(
            "QDRANT_URL", "http://qdrant.rag.svc.cluster.local:6333"
        )
        info = QdrantClient(url=qdrant_url).get_collection(collection)
        qdrant_points = info.points_count
    except Exception:
        pass

    crag_fallbacks = sum(
        1 for qr in query_results if qr["modes"]["swag_crag"]["crag_action"] == "web_fallback"
    )
    return {
        "thread_id": thread_id,
        "topic": values.get("topic"),
        "corpus": {"collection": collection, "qdrant_points": qdrant_points},
        "tool_queries_found": len(tool_queries),
        "tool_queries": tool_queries,
        "benchmark": query_results,
        "summary": {
            "queries_benchmarked": len(query_results),
            "empty_index_all_modes": all(
                all(m["indexed_hits"] == 0 for m in qr["modes"].values())
                for qr in query_results
            ),
            "swag_crag_web_fallback_rate": (
                crag_fallbacks / len(query_results) if query_results else 0.0
            ),
            "swag_rank_changes_total": sum(
                qr["modes"]["swag"]["rank_changes"] for qr in query_results
            ),
            "research_web_only": all(
                qr.get("research_actual", {}).get("web_hits", 0)
                == qr.get("research_actual", {}).get("total_hits", 0)
                for qr in query_results
                if "research_actual" in qr
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thread_id")
    parser.add_argument(
        "--langgraph-url",
        default=os.environ.get("LANGGRAPH_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--ingestion-url",
        default=os.environ.get(
            "INGESTION_API_URL", "http://ingestion.rag.svc.cluster.local:8001"
        ),
    )
    parser.add_argument("--collection", default="research_docs")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    report = benchmark_thread(
        args.thread_id,
        langgraph_url=args.langgraph_url,
        ingestion_url=args.ingestion_url,
        collection=args.collection,
        top_k=args.top_k,
    )
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
