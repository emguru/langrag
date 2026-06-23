#!/usr/bin/env python3
"""Run a fixed query battery against live ingestion (baseline vs swag + scored eval)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BENCHMARKS = Path(__file__).resolve().parent


def _search(
    client,
    ingestion_url: str,
    *,
    query: str,
    collection: str,
    top_k: int,
    mode: str,
    topic: str | None = None,
    compare: bool = False,
) -> dict:
    body: dict[str, Any] = {
        "query": query,
        "collection": collection,
        "top_k": top_k,
        "mode": mode,
        "compare": compare,
    }
    if topic:
        body["topic"] = topic
    resp = client.post(
        f"{ingestion_url.rstrip('/')}/rag/search",
        json=body,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


def _spec_key(case: dict[str, Any]) -> str:
    mode = case.get("mode", "swag")
    if mode == "swag_crag" or case.get("swag_crag"):
        return "swag_crag"
    return "swag"


def run_battery(
    ingestion_url: str,
    collection: str,
    top_k: int,
    *,
    cases: list[dict[str, Any]],
) -> dict:
    import httpx

    results = []
    with httpx.Client() as client:
        for case in cases:
            query = case["query"]
            mode = case.get("mode", "swag")
            topic = case.get("topic")
            spec_key = _spec_key(case)

            row: dict[str, Any] = {
                "id": case.get("id"),
                "query": query,
                "mode": mode,
                "topic": topic,
                "modes": {},
            }
            baseline_hits: list[dict] = []
            primary_hits: list[dict] = []
            crag_meta: dict | None = None

            if spec_key == "swag":
                baseline_data = _search(
                    client,
                    ingestion_url,
                    query=query,
                    collection=collection,
                    top_k=top_k,
                    mode="baseline",
                    topic=topic,
                )
                baseline_hits = list(baseline_data.get("hits") or [])

                swag_data = _search(
                    client,
                    ingestion_url,
                    query=query,
                    collection=collection,
                    top_k=top_k,
                    mode="swag",
                    topic=topic,
                    compare=True,
                )
                primary_hits = list(swag_data.get("hits") or [])
                if not baseline_hits:
                    baseline_hits = list(
                        swag_data.get("baseline_hits_raw")
                        or swag_data.get("baseline_hits")
                        or []
                    )
                swag = swag_data.get("swag") or {}
                rc = [
                    c
                    for c in (swag_data.get("comparison") or {}).get("rank_changes", [])
                    if c.get("rank_delta") not in (None, 0)
                ]
                lift = swag_data.get("lift") or {}
                row["modes"]["baseline"] = {
                    "hits": len(baseline_hits),
                    "top_score": round(baseline_hits[0]["score"], 4)
                    if baseline_hits
                    else None,
                }
                row["modes"]["swag"] = {
                    "hits": len(primary_hits),
                    "top_score": round(primary_hits[0]["score"], 4)
                    if primary_hits
                    else None,
                    "rank_changes": len(rc),
                    "deduped": swag.get("deduped"),
                    "noise_dropped": swag.get("noise_dropped"),
                    "lift_rerank": (lift.get("swag_rerank") or {}).get("has_positive_lift"),
                    "lift_full": (lift.get("swag_full") or {}).get("has_positive_lift"),
                }
                row["swag_moved"] = len(rc) > 0
            else:
                crag_data = _search(
                    client,
                    ingestion_url,
                    query=query,
                    collection=collection,
                    top_k=top_k,
                    mode="swag_crag",
                    topic=topic,
                    compare=True,
                )
                primary_hits = list(crag_data.get("hits") or [])
                baseline_hits = list(
                    crag_data.get("baseline_hits_raw")
                    or crag_data.get("baseline_hits")
                    or []
                )
                crag_meta = crag_data.get("crag")
                row["modes"]["swag_crag"] = {
                    "hits": len(primary_hits),
                    "top_score": round(primary_hits[0]["score"], 4)
                    if primary_hits
                    else None,
                    "crag_action": (crag_meta or {}).get("action"),
                    "crag_kept": (crag_meta or {}).get("kept"),
                }

            row["_baseline_hits"] = baseline_hits
            row["_primary_hits"] = primary_hits
            row["_crag_meta"] = crag_meta
            row["_spec_key"] = spec_key
            results.append(row)

    swag_active = sum(1 for r in results if r.get("swag_moved"))
    return {
        "queries": len(results),
        "swag_rank_changes_on": swag_active,
        "results": results,
    }


def run_scored_eval(
    battery: dict,
    *,
    cases_path: Path,
    cases: list[dict[str, Any]],
) -> dict:
    eval_dir = cases_path.parent
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    import query_eval

    sheet = query_eval.load_eval_cases(cases_path)
    top_k = int(sheet.get("top_k") or 3)
    threshold = float(sheet.get("pass_threshold") or 0.8)
    by_id = {row.get("id"): row for row in battery["results"] if row.get("id")}

    eval_results = []
    for case in cases:
        row = by_id.get(case.get("id"))
        if not row:
            eval_results.append(
                {
                    "id": case.get("id"),
                    "query": case.get("query"),
                    "passed": False,
                    "skipped": False,
                    "checks": {},
                    "details": {"missing": "case not in battery results"},
                }
            )
            continue

        spec_key = row.pop("_spec_key", _spec_key(case))
        primary_hits = row.pop("_primary_hits", [])
        baseline_hits = row.pop("_baseline_hits", [])
        crag_meta = row.pop("_crag_meta", None)

        if case.get("optional") and not primary_hits:
            eval_results.append(
                {
                    "id": case.get("id"),
                    "query": case.get("query"),
                    "passed": False,
                    "skipped": True,
                    "reason": "optional case — no hits (upload matching doc or ignore)",
                    "checks": {},
                    "details": {},
                }
            )
            continue

        eval_results.append(
            query_eval.score_case(
                case,
                swag_hits=primary_hits,
                baseline_hits=baseline_hits,
                top_k=top_k,
                crag_meta=crag_meta,
                spec_key=spec_key,
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

    eval_dir = args.cases.parent
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    import query_eval

    sheet = query_eval.load_eval_cases(args.cases)
    cases = list(sheet.get("cases") or [])

    battery = run_battery(
        args.ingestion_url,
        args.collection,
        args.top_k,
        cases=cases,
    )
    if not args.skip_eval:
        battery["eval"] = run_scored_eval(
            battery, cases_path=args.cases, cases=cases
        )
        if not args.json_only:
            print(battery["eval"]["report_text"], file=sys.stderr)

    for row in battery["results"]:
        row.pop("_baseline_hits", None)
        row.pop("_primary_hits", None)
        row.pop("_crag_meta", None)
        row.pop("_spec_key", None)

    json.dump(battery, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if not args.skip_eval:
        verdict = battery["eval"]["summary"]["verdict"]
        return 0 if verdict == "PASS" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
