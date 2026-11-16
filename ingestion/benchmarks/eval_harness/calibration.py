"""Fit per-modality vector score floors from harness qrels."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Callable

from validation_eval import hit_is_relevant

from ingestion.modality import infer_chunk_modality


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def fit_vector_calibration(
    *,
    harness_manifest: dict[str, Any],
    harness_root: Path,
    indexed: list[dict[str, Any]],
    embedder,
    vector_search: Callable[..., list[dict[str, Any]]],
    global_floor: float,
    load_json: Callable[[Path], dict[str, Any]],
    corpus_filter: list[str] | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    """Collect vector scores on qrel cases and recommend per-modality floors."""
    modality_relevant: dict[str, list[float]] = {}
    modality_irrelevant: dict[str, list[float]] = {}
    cases_run = 0

    for corpus in harness_manifest.get("corpora") or []:
        corpus_id = str(corpus.get("id"))
        if corpus_filter and corpus_id not in corpus_filter:
            continue
        qrels_path = harness_root / str(corpus["qrels"])
        sheet = load_json(qrels_path)
        topic = str(sheet.get("topic") or corpus.get("topic") or "")
        for case in sheet.get("cases") or []:
            relevant = list(case.get("relevant_seed_keys") or [])
            if not relevant:
                continue
            hits = vector_search(
                indexed,
                embedder,
                str(case["query"]),
                top_k=top_k,
                topic=topic,
            )
            cases_run += 1
            for hit in hits:
                modality = infer_chunk_modality(hit)
                score = float(hit.get("score") or 0.0)
                bucket = (
                    modality_relevant if hit_is_relevant(hit, relevant) else modality_irrelevant
                )
                bucket.setdefault(modality, []).append(score)

    fitted: dict[str, dict[str, Any]] = {}
    for modality in sorted(set(modality_relevant) | set(modality_irrelevant)):
        rel = modality_relevant.get(modality) or []
        irr = modality_irrelevant.get(modality) or []
        if rel:
            floor = round(max(0.05, _percentile(rel, 10) * 0.95), 4)
            method = "p10_relevant_x0.95"
        elif irr:
            floor = round(max(0.05, _percentile(irr, 90) * 1.05), 4)
            method = "p90_irrelevant_x1.05_fallback"
        else:
            floor = global_floor
            method = "global_fallback"
        fitted[modality] = {
            "vector_floor": floor,
            "method": method,
            "relevant_scores_n": len(rel),
            "irrelevant_scores_n": len(irr),
            "relevant_median": round(statistics.median(rel), 4) if rel else None,
            "irrelevant_median": round(statistics.median(irr), 4) if irr else None,
            "delta_vs_global": round(floor - global_floor, 4),
        }

    floors = [row["vector_floor"] for row in fitted.values()]
    spread = round(max(floors) - min(floors), 4) if floors else 0.0
    heterogeneous = spread >= 0.03 and len(floors) >= 2

    from ingestion.modality import score_floors

    live_floors = score_floors()
    live_values = list(live_floors.values())
    live_spread = round(max(live_values) - min(live_values), 4) if live_values else 0.0
    live_calibrated = live_spread >= 0.03 and len(set(live_values)) >= 2

    if heterogeneous and not live_calibrated:
        advisor = {
            "finding_id": "uncalibrated_heterogeneous_corpus",
            "severity": "error",
            "message": "mixed modalities share one global score floor",
            "apply_env": "CALIBRATION_APPLY=true",
        }
    elif heterogeneous:
        advisor = {
            "finding_id": "calibrated_heterogeneous_corpus",
            "severity": "info",
            "message": "Mixed modalities use per-modality score floors.",
            "apply_env": None,
        }
    else:
        advisor = {
            "finding_id": "homogeneous_score_floors",
            "severity": "info",
            "message": "Per-modality floors are close enough that the global floor is reasonable.",
            "apply_env": None,
        }

    return {
        "version": 1,
        "cases_run": cases_run,
        "global_floor": global_floor,
        "score_source": "vector",
        "by_modality": fitted,
        "modality_spread": spread,
        "heterogeneous_corpus": heterogeneous,
        "live_score_floors": live_floors,
        "live_floor_spread": live_spread,
        "advisor": advisor,
    }


def format_calibration_report(report: dict[str, Any]) -> str:
    lines = [
        "=== Calibration fit ===",
        f"Cases run: {report.get('cases_run', 0)}",
        f"Global floor: {report.get('global_floor')}",
        f"Modality spread: {report.get('modality_spread')}",
        f"Heterogeneous: {'yes' if report.get('heterogeneous_corpus') else 'no'}",
        "",
    ]
    for modality, row in sorted((report.get("by_modality") or {}).items()):
        lines.append(
            f"  {modality}: floor={row['vector_floor']} "
            f"(Δ global {row['delta_vs_global']:+.4f}, "
            f"rel n={row['relevant_scores_n']}, irr n={row['irrelevant_scores_n']})"
        )
    advisor = report.get("advisor") or {}
    lines.extend(
        [
            "",
            f"Advisor: {advisor.get('finding_id')} [{advisor.get('severity')}]",
            str(advisor.get("message") or ""),
            f"Apply on deploy: {advisor.get('apply_env')}",
        ]
    )
    return "\n".join(lines)


def write_calibration(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
