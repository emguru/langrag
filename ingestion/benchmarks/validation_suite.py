#!/usr/bin/env python3
"""Run qrel validation suites against live ingestion (tuning + holdout)."""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    topic: str | None,
    compare: bool,
) -> dict[str, Any]:
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


def run_suite(
    ingestion_url: str,
    sheet: dict[str, Any],
    *,
    request_delay_s: float = 2.1,
) -> dict[str, Any]:
    import httpx

    from validation_eval import evaluate_case, summarize_results

    collection = str(sheet.get("collection") or "research_docs")
    top_k = int(sheet.get("top_k") or 8)
    cases = list(sheet.get("cases") or [])
    results: list[dict[str, Any]] = []

    with httpx.Client() as client:
        for case in cases:
            response = _search(
                client,
                ingestion_url,
                query=str(case["query"]),
                collection=collection,
                top_k=top_k,
                mode=str(case.get("mode", "swag")),
                topic=case.get("topic"),
                compare=bool(case.get("compare")),
            )
            results.append(evaluate_case(case, response, top_k=top_k))
            if request_delay_s > 0:
                time.sleep(request_delay_s)

    summary = summarize_results(sheet, results)
    return {
        "sheet_path": sheet.get("_path"),
        "summary": summary,
        "cases": results,
    }


def load_sheet(path: Path) -> dict[str, Any]:
    from validation_eval import load_qrels

    sheet = load_qrels(path)
    sheet["_path"] = str(path)
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ingestion-url",
        default="http://ingestion.rag.svc.cluster.local:8001",
    )
    parser.add_argument(
        "--tuning",
        type=Path,
        default=BENCHMARKS / "validation_qrels.json",
    )
    parser.add_argument(
        "--holdout",
        type=Path,
        default=BENCHMARKS / "holdout_qrels.json",
    )
    parser.add_argument(
        "--suite",
        choices=("tuning", "holdout", "all"),
        default="all",
    )
    parser.add_argument("--request-delay", type=float, default=2.1)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    if str(BENCHMARKS) not in sys.path:
        sys.path.insert(0, str(BENCHMARKS))

    report: dict[str, Any] = {"suites": {}}
    exit_code = 0

    paths: list[tuple[str, Path]] = []
    if args.suite in ("tuning", "all"):
        paths.append(("tuning", args.tuning))
    if args.suite in ("holdout", "all"):
        paths.append(("holdout", args.holdout))

    for name, path in paths:
        sheet = load_sheet(path)
        suite_report = run_suite(
            args.ingestion_url,
            sheet,
            request_delay_s=args.request_delay,
        )
        report["suites"][name] = suite_report
        if not args.json_only:
            print(suite_report["summary"]["report_text"], file=sys.stderr)
            print(file=sys.stderr)
        if suite_report["summary"]["verdict"] != "PASS":
            exit_code = 1

    overall_pass = exit_code == 0
    report["verdict"] = "PASS" if overall_pass else "FAIL"
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
