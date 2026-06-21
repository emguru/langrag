#!/usr/bin/env python3
"""Run a fixed query battery against live ingestion (baseline vs swag + scored eval)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parent

QUERIES = [
    "what is rag",
    "What is retrieval augmented generation?",
    "How does chunking affect RAG quality?",
    "What is corrective RAG?",
    "How do embeddings work in vector search?",
    "Compare naive RAG vs agentic RAG",
    "What are limitations of RAG?",
    "How to evaluate RAG systems?",
    "What is hybrid search?",
    "How does prompt engineering improve search?",
]


def run_battery(ingestion_url: str, collection: str, top_k: int) -> dict:
    import httpx

    results = []
    for query in QUERIES:
        row: dict = {"query": query, "modes": {}}
        baseline_hits: list[dict] = []
        swag_hits: list[dict] = []
        for mode in ("baseline", "swag"):
            data = httpx.post(
                f"{ingestion_url.rstrip('/')}/rag/search",
                json={
                    "query": query,
                    "collection": collection,
                    "top_k": top_k,
                    "mode": mode,
                    "compare": True,
                },
                timeout=180,
            ).json()
            hits = data.get("hits") or []
            if mode == "baseline":
                baseline_hits = list(data.get("baseline_hits") or hits)
            else:
                swag_hits = hits
            base = data.get("baseline_hits") or []
            swag = data.get("swag") or {}
            rc = [
                c
                for c in (data.get("comparison") or {}).get("rank_changes", [])
                if c.get("rank_delta") not in (None, 0)
            ]
            sources = set()
            for h in hits:
                src = str(h.get("source") or "")
                parts = src.rstrip("/").split("/")
                sources.add(parts[-2] if len(parts) >= 2 else src)
            row["modes"][mode] = {
                "hits": len(hits),
                "top_score": round(hits[0]["score"], 4) if hits else None,
                "unique_sources_top_k": len(sources),
                "rank_changes": len(rc) if mode == "swag" else 0,
                "deduped": swag.get("deduped"),
                "noise_dropped": swag.get("noise_dropped"),
                "diversity_skipped": swag.get("diversity_skipped"),
                "top_swag": round(hits[0].get("swag_score") or 0, 4)
                if hits and mode == "swag"
                else None,
            }
        row["swag_moved"] = row["modes"]["swag"]["rank_changes"] > 0
        row["source_diversity_gain"] = (
            row["modes"]["swag"]["unique_sources_top_k"]
            > row["modes"]["baseline"]["unique_sources_top_k"]
        )
        row["_baseline_hits"] = baseline_hits
        row["_swag_hits"] = swag_hits
        results.append(row)

    swag_active = sum(1 for r in results if r["swag_moved"])
    diversity_gain = sum(1 for r in results if r["source_diversity_gain"])
    return {
        "queries": len(QUERIES),
        "swag_rank_changes_on": swag_active,
        "source_diversity_improved_on": diversity_gain,
        "results": results,
    }


def run_scored_eval(
    battery: dict,
    *,
    cases_path: Path,
) -> dict:
    eval_dir = cases_path.parent
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    import query_eval

    sheet = query_eval.load_eval_cases(cases_path)
    top_k = int(sheet.get("top_k") or 3)
    threshold = float(sheet.get("pass_threshold") or 0.8)
    by_query = {row["query"]: row for row in battery["results"]}

    eval_results = []
    for case in sheet.get("cases") or []:
        query = case["query"]
        row = by_query.get(query)
        if not row:
            eval_results.append(
                {
                    "id": case.get("id"),
                    "query": query,
                    "passed": False,
                    "checks": {},
                    "details": {"missing": "query not in battery"},
                }
            )
            continue
        eval_results.append(
            query_eval.score_case(
                case,
                swag_hits=row.pop("_swag_hits", []),
                baseline_hits=row.pop("_baseline_hits", []),
                top_k=top_k,
            )
        )

    summary = query_eval.summarize_eval(eval_results, threshold)
    return {
        "summary": summary,
        "cases": eval_results,
        "report_text": query_eval.format_eval_report(summary, eval_results),
        "sheet": {
            "path": str(cases_path),
            "top_k": top_k,
            "pass_threshold": threshold,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ingestion-url",
        default="http://ingestion.rag.svc.cluster.local:8001",
    )
    parser.add_argument("--collection", default="research_docs")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--cases",
        type=Path,
        default=BENCHMARKS / "query_eval_cases.json",
        help="Eval sheet JSON (expected winners / checks per query)",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Only run rank-change metrics, not scored eval",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Suppress human-readable eval summary on stderr",
    )
    args = parser.parse_args()

    battery = run_battery(args.ingestion_url, args.collection, args.top_k)
    if not args.skip_eval:
        battery["eval"] = run_scored_eval(battery, cases_path=args.cases)
        if not args.json_only:
            print(battery["eval"]["report_text"], file=sys.stderr)

    for row in battery["results"]:
        row.pop("_baseline_hits", None)
        row.pop("_swag_hits", None)

    json.dump(battery, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if not args.skip_eval:
        verdict = battery["eval"]["summary"]["verdict"]
        return 0 if verdict == "PASS" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
