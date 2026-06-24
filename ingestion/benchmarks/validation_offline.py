#!/usr/bin/env python3
"""Offline SWAG rerank validation — fixture corpus, no HTTP/Qdrant."""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any

BENCHMARKS = Path(__file__).resolve().parent
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


def _strip_internal(hit: dict) -> dict:
    return {key: value for key, value in hit.items() if not key.startswith("_")}


def build_fixture_index(manifest: dict, fixtures_root: Path) -> tuple[list[dict], Any]:
    from ingestion.config import settings
    from ingestion.pipeline import chunk_documents, extract_text_from_bytes, get_embedder
    from ingestion.swag.profile import SWAGProfileGenerator

    prior_mode = settings.swag_profile_mode
    settings.swag_profile_mode = "heuristic"
    embedder = get_embedder()
    generator = SWAGProfileGenerator()
    indexed: list[dict] = []

    try:
        for entry in manifest.get("fixtures") or []:
            seed_key = str(entry["seed_key"])
            topic = str(entry.get("topic") or "")
            rel_path = Path(str(entry["path"]))
            path = fixtures_root / rel_path
            if not path.is_file():
                raise FileNotFoundError(f"fixture missing: {path}")
            source = f"validation://{seed_key}"
            pages = extract_text_from_bytes(path.read_bytes(), path.name)
            chunks = chunk_documents(pages, source=source)
            if not chunks:
                continue
            texts = [chunk["text"] for chunk in chunks]
            vectors = embedder.embed_documents(texts)
            for chunk, vector, text in zip(chunks, vectors, texts):
                profile = generator.generate_chunk_profile(
                    chunk_text=text,
                    source_filename=seed_key.split("/")[-1],
                    page_number=chunk.get("page"),
                )
                indexed.append(
                    {
                        "id": str(uuid.uuid4()),
                        "text": text,
                        "source": source,
                        "seed_key": seed_key,
                        "topic": topic,
                        "page": chunk.get("page"),
                        "chunk_index": chunk.get("chunk_index"),
                        "score": 0.0,
                        "swag_profile": profile.model_dump() if profile else None,
                        "_vector": vector,
                    }
                )
    finally:
        settings.swag_profile_mode = prior_mode

    return indexed, embedder


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
    results = []
    for sim, hit in scored[:top_k]:
        row = dict(hit)
        row["score"] = (sim + 1.0) / 2.0
        results.append(row)
    return results


def search_case(
    indexed: list[dict],
    embedder,
    case: dict[str, Any],
    *,
    top_k: int,
    candidate_k: int,
) -> dict[str, Any]:
    from ingestion.lift_metrics import compute_lift_metrics
    from ingestion.swag.rerank import rerank_hits_with_swag

    query = str(case["query"])
    topic = case.get("topic")
    pool = vector_search(indexed, embedder, query, candidate_k, topic=topic)
    baseline_raw = [_strip_internal(hit) for hit in pool[:top_k]]
    swag_hits, meta = rerank_hits_with_swag(query, pool, top_k=top_k)
    hits = [_strip_internal(hit) for hit in swag_hits]
    lift = {
        "swag_rerank": compute_lift_metrics(
            baseline_raw,
            hits,
            top_k=top_k,
            pipeline="swag_rerank",
            extra=meta,
        )
    }
    return {
        "hits": hits,
        "baseline_hits": baseline_raw,
        "baseline_hits_raw": baseline_raw,
        "lift": lift,
    }


def run_suite(
    indexed: list[dict],
    embedder,
    sheet: dict[str, Any],
    *,
    candidate_k: int,
) -> dict[str, Any]:
    from validation_eval import evaluate_case, summarize_results

    top_k = int(sheet.get("top_k") or 8)
    results = [
        evaluate_case(
            case,
            search_case(indexed, embedder, case, top_k=top_k, candidate_k=candidate_k),
            top_k=top_k,
        )
        for case in sheet.get("cases") or []
    ]
    return {
        "sheet_path": sheet.get("_path"),
        "summary": summarize_results(sheet, results),
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
        "--manifest",
        type=Path,
        default=BENCHMARKS / "validation_manifest.json",
    )
    parser.add_argument("--fixtures-root", type=Path, required=True)
    parser.add_argument(
        "--rerank",
        type=Path,
        default=BENCHMARKS / "validation_qrels_rerank.json",
    )
    parser.add_argument(
        "--holdout-fixtures",
        type=Path,
        default=BENCHMARKS / "holdout_fixtures_qrels.json",
    )
    parser.add_argument(
        "--suite",
        choices=("rerank", "holdout", "all"),
        default="all",
    )
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    if str(BENCHMARKS) not in sys.path:
        sys.path.insert(0, str(BENCHMARKS))
    if str(ROOT / "ingestion" / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "ingestion" / "src"))

    with args.manifest.open(encoding="utf-8") as handle:
        manifest = json.load(handle)

    indexed, embedder = build_fixture_index(manifest, args.fixtures_root)
    if not indexed:
        print("No chunks indexed from fixtures", file=sys.stderr)
        return 2

    paths: list[tuple[str, Path]] = []
    if args.suite in ("rerank", "all"):
        paths.append(("rerank", args.rerank))
    if args.suite in ("holdout", "all"):
        paths.append(("holdout_fixtures", args.holdout_fixtures))

    report: dict[str, Any] = {
        "stack": "kube_offline_rerank",
        "chunks_indexed": len(indexed),
        "suites": {},
    }
    exit_code = 0
    for name, path in paths:
        if not path.is_file():
            print(f"skip missing sheet: {path}", file=sys.stderr)
            continue
        sheet = load_sheet(path)
        suite_report = run_suite(
            indexed,
            embedder,
            sheet,
            candidate_k=args.candidate_k,
        )
        report["suites"][name] = suite_report
        if not args.json_only:
            print(suite_report["summary"]["report_text"], file=sys.stderr)
            print(file=sys.stderr)
        if suite_report["summary"]["verdict"] != "PASS":
            exit_code = 1

    report["verdict"] = "PASS" if exit_code == 0 else "FAIL"
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
