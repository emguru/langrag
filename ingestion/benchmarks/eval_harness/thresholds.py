"""Brutal pass/fail gates for SWAG vs strong baselines."""

from __future__ import annotations

from typing import Any


def _pct_delta(swag: float, baseline: float) -> float:
    if baseline <= 0:
        return 100.0 if swag > 0 else 0.0
    return 100.0 * (swag - baseline) / baseline


def check_swag_thresholds(
    report: dict[str, Any],
    *,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    by_corpus = report.get("by_corpus") or {}
    metric = str(thresholds.get("primary_metric") or "recall_at_k")
    baseline = str(thresholds.get("compare_baseline") or "hybrid_rrf")
    min_improve = float(thresholds.get("min_domain_improvement_pct") or 10.0)
    min_domains = int(thresholds.get("min_domains_improved") or 4)
    hard_categories = list(thresholds.get("hard_categories") or ["synonym", "acronym", "version_trap"])
    min_hard_improve = float(thresholds.get("min_hard_category_improvement_pct") or 15.0)

    domain_checks: list[dict[str, Any]] = []
    improved_domains = 0
    for corpus_id, block in by_corpus.items():
        swag_val = float(((block.get("methods") or {}).get("swag") or {}).get(metric) or 0.0)
        base_val = float(((block.get("methods") or {}).get(baseline) or {}).get(metric) or 0.0)
        delta = _pct_delta(swag_val, base_val)
        ok = delta >= min_improve
        if ok:
            improved_domains += 1
        domain_checks.append(
            {
                "corpus_id": corpus_id,
                "metric": metric,
                "baseline": baseline,
                "swag": round(swag_val, 4),
                "baseline_value": round(base_val, 4),
                "improvement_pct": round(delta, 2),
                "passed": ok,
            }
        )

    hard_rows = report.get("by_category") or {}
    hard_checks: list[dict[str, Any]] = []
    hard_pass = True
    for category in hard_categories:
        row = hard_rows.get(category) or {}
        swag_val = float((row.get("swag") or {}).get(metric) or 0.0)
        base_val = float((row.get(baseline) or {}).get(metric) or 0.0)
        delta = _pct_delta(swag_val, base_val)
        ok = delta >= min_hard_improve
        hard_pass = hard_pass and ok
        hard_checks.append(
            {
                "category": category,
                "improvement_pct": round(delta, 2),
                "passed": ok,
            }
        )

    latency = report.get("latency") or {}
    swag_ms = float(latency.get("swag_mean_ms") or 0.0)
    vector_ms = float(latency.get("vector_mean_ms") or 1.0)
    max_mult = float(thresholds.get("max_latency_multiplier_vs_vector") or 3.0)
    latency_ok = vector_ms <= 0 or (swag_ms / vector_ms) <= max_mult

    wrong_version = report.get("wrong_version") or {}
    swag_wrong = float(wrong_version.get("swag_mean") or 1.0)
    base_wrong = float(wrong_version.get(f"{baseline}_mean") or 1.0)
    version_ok = swag_wrong <= base_wrong

    verdict = (
        improved_domains >= min_domains
        and hard_pass
        and latency_ok
        and version_ok
    )

    lines = [
        "=== SWAG brutal thresholds ===",
        f"Domains improved (>={min_improve:.0f}% vs {baseline}): {improved_domains}/{len(domain_checks)} (need {min_domains})",
        f"Hard categories (>={min_hard_improve:.0f}%): {'PASS' if hard_pass else 'FAIL'}",
        f"Latency multiplier: {swag_ms / max(vector_ms, 1):.2f}x (max {max_mult:.1f}x) — {'PASS' if latency_ok else 'FAIL'}",
        f"Wrong-version rate: SWAG {swag_wrong:.3f} vs {baseline} {base_wrong:.3f} — {'PASS' if version_ok else 'FAIL'}",
        f"Verdict: {'SWAG READY SIGNAL' if verdict else 'NOT PRODUCT-WORTHY YET'}",
    ]

    return {
        "verdict": "PASS" if verdict else "FAIL",
        "product_ready_signal": verdict,
        "domain_checks": domain_checks,
        "hard_category_checks": hard_checks,
        "latency_ok": latency_ok,
        "wrong_version_ok": version_ok,
        "report_text": "\n".join(lines),
    }
