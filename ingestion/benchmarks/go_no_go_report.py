#!/usr/bin/env python3
"""Fast go/no-go validation — offline by default, optional cheap live smoke."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INGESTION = ROOT / "ingestion"
BENCHMARKS = INGESTION / "benchmarks"
TESTS = INGESTION / "tests"

DEFAULT_LIVE_QUERY = "What is retrieval augmented generation?"
DEFAULT_THREAD = "019ee7b1-fdf4-7380-9366-1cfcdfc938db"


def _run_pytest() -> dict[str, Any]:
    venv_py = INGESTION / ".venv" / "bin" / "python"
    python = str(venv_py if venv_py.exists() else sys.executable)
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(INGESTION / "src")
    proc = subprocess.run(
        [python, "-m", "pytest", str(TESTS), "-q", "--tb=no"],
        cwd=str(INGESTION),
        env=env,
        capture_output=True,
        text=True,
    )
    passed = proc.returncode == 0
    summary = (proc.stdout or proc.stderr or "").strip().splitlines()
    return {
        "passed": passed,
        "exit_code": proc.returncode,
        "summary_line": summary[-1] if summary else "",
    }


def _run_swag_integrity() -> dict[str, Any]:
    sys.path.insert(0, str(INGESTION / "src"))
    sys.path.insert(0, str(BENCHMARKS.parent))

    from benchmarks.swag_integrity_benchmark import build_cases, delta, evaluate
    from benchmarks.legacy_swag_scoring import score_swag_alignment as legacy_score
    from ingestion.swag.scoring import score_swag_alignment as hardened_score

    cases = build_cases()
    legacy = evaluate("legacy", legacy_score, cases)
    hardened = evaluate("hardened", hardened_score, cases)
    changes = delta(hardened, legacy)

    hs = hardened["summary"]
    ls = legacy["summary"]
    checks = {
        "precision_at_1_held": hs["precision_at_1"] >= ls["precision_at_1"],
        "adversarial_swag_reduced": hs["adversarial_swag_mean"] < ls["adversarial_swag_mean"],
        "adversarial_rate_not_worse": hs["adversarial_rate_at_k"] <= ls["adversarial_rate_at_k"],
    }
    passed = all(checks.values())

    return {
        "passed": passed,
        "checks": checks,
        "legacy": ls,
        "hardened": hs,
        "delta": changes,
    }


def _live_smoke(
    *,
    query: str,
    ingestion_url: str,
    langgraph_url: str | None,
    thread_id: str | None,
) -> dict[str, Any]:
    import httpx

    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    # Health
    health = httpx.get(f"{ingestion_url.rstrip('/')}/health", timeout=15)
    checks["ingestion_healthy"] = health.status_code == 200

    # Corpus size
    stats = httpx.get(
        f"{ingestion_url.rstrip('/')}/rag/stats",
        params={"collection": "research_docs"},
        timeout=15,
    )
    stats.raise_for_status()
    points = stats.json().get("total_points") or 0
    details["corpus_points"] = points
    checks["corpus_non_empty"] = points > 0

    # Baseline search (Titan embed only — cheapest retrieval check)
    base = httpx.post(
        f"{ingestion_url.rstrip('/')}/rag/search",
        json={
            "query": query,
            "collection": "research_docs",
            "top_k": 3,
            "mode": "baseline",
            "compare": False,
        },
        timeout=120,
    )
    base.raise_for_status()
    base_hits = base.json().get("hits") or []
    details["baseline_hits"] = len(base_hits)
    details["baseline_top_score"] = base_hits[0].get("score") if base_hits else None
    checks["baseline_returns_hits"] = len(base_hits) > 0

    # SWAG search (1 Sonnet query profile if chunk profiles cached)
    swag = httpx.post(
        f"{ingestion_url.rstrip('/')}/rag/search",
        json={
            "query": query,
            "collection": "research_docs",
            "top_k": 3,
            "mode": "swag",
            "compare": False,
        },
        timeout=180,
    )
    swag.raise_for_status()
    swag_body = swag.json()
    swag_hits = swag_body.get("hits") or []
    swag_meta = swag_body.get("swag") or {}
    details["swag_profiles_available"] = swag_meta.get("profiles_available")
    details["swag_profiles_generated"] = swag_meta.get("profiles_generated")
    details["top_swag_scores"] = [
        round(h.get("swag_score") or 0, 4) for h in swag_hits[:3]
    ]
    checks["swag_profiles_loaded"] = (swag_meta.get("profiles_available") or 0) > 0
    checks["swag_score_nonzero"] = any(
        (h.get("swag_score") or 0) > 0 for h in swag_hits
    )
    checks["swag_no_runtime_profiling"] = (swag_meta.get("profiles_generated") or 0) == 0

    # Optional thread readability (no extra Bedrock)
    if langgraph_url and thread_id:
        try:
            resp = httpx.post(
                f"{langgraph_url.rstrip('/')}/threads/search",
                json={"limit": 50},
                timeout=30,
            )
            resp.raise_for_status()
            thread = next(
                (t for t in resp.json() if t.get("thread_id") == thread_id),
                None,
            )
            if thread:
                values = thread.get("values") or {}
                details["thread_topic"] = values.get("topic")
                details["thread_findings"] = len(values.get("findings") or [])
                checks["thread_has_findings"] = details["thread_findings"] > 0
            else:
                checks["thread_has_findings"] = False
        except Exception as exc:
            details["thread_error"] = str(exc)
            checks["thread_has_findings"] = False

    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "details": details,
        "bedrock_calls_estimate": "~2 (1 Titan embed ×2 searches + 1 Sonnet query profile)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help="Run minimal live checks (~2 searches, ~3 Bedrock calls)",
    )
    parser.add_argument(
        "--ingestion-url",
        default="http://ingestion.rag.svc.cluster.local:8001",
    )
    parser.add_argument("--langgraph-url", default="http://127.0.0.1:8000")
    parser.add_argument("--thread-id", default=DEFAULT_THREAD)
    parser.add_argument("--query", default=DEFAULT_LIVE_QUERY)
    args = parser.parse_args()

    report: dict[str, Any] = {
        "verdict": "NO-GO",
        "phases": {},
    }

    pytest_result = _run_pytest()
    report["phases"]["unit_tests"] = pytest_result

    integrity_result = _run_swag_integrity()
    report["phases"]["swag_integrity"] = integrity_result

    from benchmarks.offline_swag_smoke import offline_heuristic_rerank_smoke

    heuristic_result = offline_heuristic_rerank_smoke()
    report["phases"]["offline_swag_rerank"] = heuristic_result

    offline_pass = (
        pytest_result["passed"]
        and integrity_result["passed"]
        and heuristic_result.get("passed", False)
    )
    report["phases"]["offline"] = {"passed": offline_pass}

    live_pass = True
    if args.live_smoke:
        try:
            live_result = _live_smoke(
                query=args.query,
                ingestion_url=args.ingestion_url,
                langgraph_url=args.langgraph_url,
                thread_id=args.thread_id,
            )
        except Exception as exc:
            live_result = {"passed": False, "error": str(exc), "checks": {}}
        report["phases"]["live_smoke"] = live_result
        live_pass = live_result.get("passed", False)
    else:
        report["phases"]["live_smoke"] = {
            "skipped": True,
            "note": "Pass --live-smoke for baseline retrieval only (~1 Titan embed). SWAG tested offline.",
        }

    if offline_pass and (live_pass or not args.live_smoke):
        report["verdict"] = "GO" if args.live_smoke else "GO-OFFLINE"
    else:
        report["verdict"] = "NO-GO"

    print(json.dumps(report, indent=2))
    print()
    print("=== Go / No-Go ===")
    print(f"Verdict: {report['verdict']}")
    print(f"  unit tests:        {'PASS' if pytest_result['passed'] else 'FAIL'}")
    print(f"  SWAG integrity:    {'PASS' if integrity_result['passed'] else 'FAIL'}")
    hr = report["phases"].get("offline_swag_rerank", {})
    print(
        f"  offline SWAG rerank: {'PASS' if hr.get('passed') else 'FAIL'}"
        f" (heuristic, $0)"
    )
    if args.live_smoke:
        lr = report["phases"]["live_smoke"]
        print(f"  live smoke:        {'PASS' if lr.get('passed') else 'FAIL'}")
        if lr.get("details"):
            d = lr["details"]
            print(f"    corpus points:   {d.get('corpus_points')}")
            print(f"    SWAG profiles:   {d.get('swag_profiles_available')}")
            print(f"    top SWAG scores: {d.get('top_swag_scores')}")
    else:
        print("  live smoke:        SKIPPED (use --live-smoke against cluster)")

    if report["verdict"] == "GO-OFFLINE":
        print()
        print("Offline bar passed. Run once against AWS before spending more:")
        print("  ./scripts/validate-project.sh --live-smoke")

    return 0 if report["verdict"].startswith("GO") else 1


if __name__ == "__main__":
    raise SystemExit(main())
