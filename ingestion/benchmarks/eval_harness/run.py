#!/usr/bin/env python3
"""SWAG Evaluation Harness — offline multi-corpus, multi-baseline comparison."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HARNESS_ROOT = Path(__file__).resolve().parent
BENCHMARKS = HARNESS_ROOT.parent
ROOT = BENCHMARKS.parent.parent


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def vector_search(
    indexed: list[dict],
    embedder,
    query: str,
    top_k: int,
    topic: str | None = None,
) -> list[dict]:
    query_vec = embedder.embed_documents([query])[0]
    scored: list[tuple[float, dict]] = []
    for hit in indexed:
        if topic and str(hit.get("topic") or "") != topic:
            continue
        sim = _cosine(query_vec, hit["_vector"])
        scored.append((sim, hit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    results: list[dict] = []
    for sim, hit in scored[:top_k]:
        row = dict(hit)
        row["score"] = (sim + 1.0) / 2.0
        results.append(row)
    return results


def run_baseline(
    name: str,
    *,
    bm25_by_topic: dict,
    indexed: list[dict],
    embedder,
    query: str,
    topic: str | None,
    top_k: int,
    pool_k: int,
) -> list[dict]:
    from eval_harness.baselines import (
        run_bm25,
        run_hybrid_rerank,
        run_hybrid_rrf,
        run_swag,
        run_vector,
    )

    bm25 = bm25_by_topic.get(topic or "")
    if name == "bm25":
        if bm25 is None:
            return []
        return run_bm25(bm25, query, topic=topic, top_k=top_k)
    if name == "vector":
        return run_vector(
            vector_search, indexed, embedder, query, topic=topic, top_k=top_k
        )
    if bm25 is None:
        bm25 = bm25_by_topic.get(next(iter(bm25_by_topic), ""), None)
    if name == "hybrid_rrf":
        return run_hybrid_rrf(
            bm25,
            vector_search,
            indexed,
            embedder,
            query,
            topic=topic,
            top_k=top_k,
            pool_k=pool_k,
        )
    if name == "hybrid_rerank":
        return run_hybrid_rerank(
            bm25,
            vector_search,
            indexed,
            embedder,
            query,
            topic=topic,
            top_k=top_k,
            pool_k=pool_k,
        )
    if name == "swag":
        return run_swag(
            vector_search,
            indexed,
            embedder,
            query,
            topic=topic,
            top_k=top_k,
            pool_k=pool_k,
        )
    raise ValueError(f"unknown baseline: {name}")


def evaluate_corpus(
    corpus: dict[str, Any],
    sheet: dict[str, Any],
    *,
    indexed: list[dict],
    embedder,
    bm25_by_topic: dict,
    baselines: list[str],
    pool_k: int,
    generate: bool = False,
    generate_mode: str = "extractive",
    generate_methods: list[str] | None = None,
) -> dict[str, Any]:
    from eval_harness.answer_metrics import score_answer
    from eval_harness.generation import generate_answer
    from eval_harness.metrics import aggregate_metric_rows, score_retrieval

    topic = str(sheet.get("topic") or corpus.get("topic") or "")
    top_k = int(sheet.get("top_k") or 5)
    gen_methods = set(generate_methods or ["swag", "hybrid_rrf"])
    case_rows: list[dict[str, Any]] = []
    latency_ms: dict[str, list[float]] = {name: [] for name in baselines}

    for case in sheet.get("cases") or []:
        case = dict(case)
        case["topic"] = topic
        per_method: dict[str, Any] = {}
        for name in baselines:
            start = time.perf_counter()
            hits = run_baseline(
                name,
                bm25_by_topic=bm25_by_topic,
                indexed=indexed,
                embedder=embedder,
                query=str(case["query"]),
                topic=topic,
                top_k=top_k,
                pool_k=pool_k,
            )
            latency_ms[name].append((time.perf_counter() - start) * 1000.0)
            metrics = score_retrieval(hits, case, k=top_k)
            if generate and name in gen_methods:
                answer, gen_meta = generate_answer(
                    str(case["query"]),
                    hits,
                    mode=generate_mode,
                )
                metrics.update(
                    score_answer(
                        answer,
                        case,
                        hits=hits,
                        generation_meta=gen_meta,
                    )
                )
            per_method[name] = metrics
        case_rows.append(
            {
                "id": case.get("id"),
                "category": case.get("category"),
                "query": case.get("query"),
                "expected_answer": case.get("expected_answer"),
                "methods": per_method,
            }
        )

    by_method: dict[str, dict[str, float]] = {}
    for name in baselines:
        by_method[name] = aggregate_metric_rows(
            [row["methods"][name] for row in case_rows]
        )

    by_category: dict[str, dict[str, dict[str, float]]] = {}
    for row in case_rows:
        category = str(row.get("category") or "unknown")
        bucket = by_category.setdefault(category, {})
        for name in baselines:
            bucket.setdefault(name, [])
            bucket[name].append(row["methods"][name])
    by_category_agg: dict[str, dict[str, dict[str, float]]] = {}
    for category, bucket in by_category.items():
        by_category_agg[category] = {
            name: aggregate_metric_rows(rows) for name, rows in bucket.items()
        }

    return {
        "corpus_id": corpus.get("id"),
        "topic": topic,
        "cases_run": len(case_rows),
        "methods": by_method,
        "by_category": by_category_agg,
        "latency_ms_mean": {
            name: sum(values) / len(values) if values else 0.0
            for name, values in latency_ms.items()
        },
        "cases": case_rows,
    }


def build_report(
    harness_manifest: dict[str, Any],
    corpus_reports: list[dict[str, Any]],
    *,
    baselines: list[str],
    generate_methods: list[str] | None = None,
) -> dict[str, Any]:
    from eval_harness.metrics import aggregate_metric_rows

    by_corpus = {row["corpus_id"]: row for row in corpus_reports}
    global_by_method: dict[str, dict[str, float]] = {}
    for name in baselines:
        global_by_method[name] = aggregate_metric_rows(
            [row["methods"][name] for row in corpus_reports]
        )

    by_category: dict[str, dict[str, dict[str, float]]] = {}
    for row in corpus_reports:
        for category, methods in (row.get("by_category") or {}).items():
            bucket = by_category.setdefault(category, {})
            for name, metrics in methods.items():
                bucket.setdefault(name, []).append(metrics)
    by_category_agg = {
        category: {name: aggregate_metric_rows(rows) for name, rows in methods.items()}
        for category, methods in by_category.items()
    }

    latency = {
        f"{name}_mean_ms": sum(r["latency_ms_mean"].get(name, 0.0) for r in corpus_reports)
        / max(len(corpus_reports), 1)
        for name in baselines
    }
    wrong_version = {}
    for name in baselines:
        wrong_version[f"{name}_mean"] = global_by_method[name].get("wrong_version_rate", 0.0)

    report: dict[str, Any] = {
        "harness_version": harness_manifest.get("version"),
        "pilot": True,
        "corpora_run": len(corpus_reports),
        "baselines": baselines,
        "global": global_by_method,
        "by_corpus": by_corpus,
        "by_category": by_category_agg,
        "latency": latency,
        "wrong_version": wrong_version,
    }

    gen_methods = list(generate_methods or [])
    if gen_methods:
        answer_keys = (
            "answer_correctness",
            "answer_citation_correct",
            "hallucination_rate",
            "grounded_answer_rate",
        )
        generation_global: dict[str, dict[str, float]] = {}
        for name in gen_methods:
            rows = []
            for corp in corpus_reports:
                for case in corp.get("cases") or []:
                    method_metrics = (case.get("methods") or {}).get(name) or {}
                    if "answer_correctness" in method_metrics:
                        rows.append({k: method_metrics[k] for k in answer_keys})
            generation_global[name] = aggregate_metric_rows(rows) if rows else {}
        report["generation"] = {
            "mode": harness_manifest.get("_generate_mode", "extractive"),
            "methods": gen_methods,
            "global": generation_global,
        }

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--harness-root",
        type=Path,
        default=HARNESS_ROOT,
    )
    parser.add_argument(
        "--fixtures-root",
        type=Path,
        default=ROOT / "fixtures" / "corpus",
    )
    parser.add_argument(
        "--corpus",
        action="append",
        help="Run only this corpus id (repeatable)",
    )
    parser.add_argument("--pool-k", type=int, default=20)
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Score answer behavior from expected_answer + citation qrel fields",
    )
    parser.add_argument(
        "--generate-mode",
        choices=("extractive", "llm"),
        default="extractive",
        help="How to compose answers from retrieved hits (default: extractive, no LLM cost)",
    )
    parser.add_argument(
        "--generate-method",
        action="append",
        dest="generate_methods",
        help="Retrieval methods to evaluate for answers (repeatable; default: swag, hybrid_rrf)",
    )
    parser.add_argument(
        "--results-out",
        type=Path,
        default=HARNESS_ROOT / "results" / f"run-{uuid.uuid4().hex[:8]}.json",
    )
    args = parser.parse_args()

    generate_methods = args.generate_methods or ["swag", "hybrid_rrf"]

    for path in (str(BENCHMARKS), str(ROOT / "ingestion" / "src"), str(args.harness_root)):
        if path not in sys.path:
            sys.path.insert(0, path)

    from eval_harness.index_builder import build_harness_index, load_json
    from eval_harness.answer_metrics import check_answer_thresholds
    from eval_harness.thresholds import check_swag_thresholds

    harness_manifest = load_json(args.harness_root / "manifest.json")
    if args.generate:
        harness_manifest["_generate_mode"] = args.generate_mode
    baselines = list(harness_manifest.get("baselines") or [])
    indexed, embedder, bm25_by_topic = build_harness_index(
        harness_manifest,
        harness_root=args.harness_root,
        fixtures_root=args.fixtures_root,
    )
    if not indexed:
        print("No chunks indexed", file=sys.stderr)
        return 2

    corpus_reports: list[dict[str, Any]] = []
    for corpus in harness_manifest.get("corpora") or []:
        corpus_id = str(corpus.get("id"))
        if args.corpus and corpus_id not in args.corpus:
            continue
        qrels_path = args.harness_root / str(corpus["qrels"])
        sheet = load_json(qrels_path)
        corpus_reports.append(
            evaluate_corpus(
                corpus,
                sheet,
                indexed=indexed,
                embedder=embedder,
                bm25_by_topic=bm25_by_topic,
                baselines=baselines,
                pool_k=args.pool_k,
                generate=args.generate,
                generate_mode=args.generate_mode,
                generate_methods=generate_methods if args.generate else None,
            )
        )

    report = build_report(
        harness_manifest,
        corpus_reports,
        baselines=baselines,
        generate_methods=generate_methods if args.generate else None,
    )
    thresholds = dict(harness_manifest.get("thresholds") or {})
    thresholds.setdefault("compare_baseline", "hybrid_rrf")
    thresholds.setdefault("primary_metric", "recall_at_k")
    report["thresholds"] = check_swag_thresholds(report, thresholds=thresholds)

    exit_code = 0 if report["thresholds"]["verdict"] == "PASS" else 1
    if args.generate:
        answer_thresholds = dict(harness_manifest.get("answer_thresholds") or {})
        report["answer_thresholds"] = check_answer_thresholds(
            report, thresholds=answer_thresholds
        )
        if report["answer_thresholds"]["verdict"] != "PASS":
            exit_code = 1

    args.results_out.parent.mkdir(parents=True, exist_ok=True)
    args.results_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if not args.json_only:
        print(report["thresholds"]["report_text"], file=sys.stderr)
        if args.generate and report.get("answer_thresholds"):
            print(file=sys.stderr)
            print(report["answer_thresholds"]["report_text"], file=sys.stderr)
        print(file=sys.stderr)
        for corpus_id, block in report["by_corpus"].items():
            swag = block["methods"].get("swag", {})
            hybrid = block["methods"].get("hybrid_rrf", {})
            line = (
                f"{corpus_id}: recall@5 swag={swag.get('recall_at_k', 0):.3f} "
                f"hybrid_rrf={hybrid.get('recall_at_k', 0):.3f}"
            )
            if args.generate:
                line += (
                    f" | answer swag={swag.get('answer_correctness', 0):.2f} "
                    f"hybrid={hybrid.get('answer_correctness', 0):.2f}"
                )
            print(line, file=sys.stderr)
        print(f"Results: {args.results_out}", file=sys.stderr)

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
