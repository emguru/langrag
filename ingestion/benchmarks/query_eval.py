"""Score live SWAG retrieval against labeled expectations (demo corpus eval sheet)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).with_name("query_eval_cases.json")

BAD_BASELINE_PREFIXES = ("# ", "![", "<html", "<!DOCTYPE")


def load_eval_cases(path: Path | None = None) -> dict[str, Any]:
    with (path or CASES_PATH).open(encoding="utf-8") as handle:
        return json.load(handle)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _hit_text(hit: dict) -> str:
    return str(hit.get("text") or "")


def _hit_kind(hit: dict) -> str | None:
    kind = hit.get("chunk_kind")
    if kind:
        return str(kind)
    profile = hit.get("swag_profile") or {}
    if isinstance(profile, dict) and profile.get("chunk_kind"):
        return str(profile["chunk_kind"])
    return None


def _looks_like_title_chunk(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(BAD_BASELINE_PREFIXES):
        return True
    if stripped.startswith("# ") and len(stripped.splitlines()) <= 2:
        return True
    return False


def _baseline_top_is_bad(baseline_hits: list[dict]) -> bool:
    if not baseline_hits:
        return False
    top = baseline_hits[0]
    kind = _hit_kind(top)
    if kind in {"title", "nav", "boilerplate"}:
        return True
    return _looks_like_title_chunk(_hit_text(top))


def _text_matches_any(text: str, needles: list[str]) -> bool:
    lowered = _norm(text)
    return any(_norm(needle) in lowered for needle in needles)


def _check_min_hits(hits: list[dict], minimum: int) -> tuple[bool, str]:
    if len(hits) >= minimum:
        return True, f"{len(hits)} hits (>={minimum})"
    return False, f"only {len(hits)} hits (need >={minimum})"


def _check_top1_kind(hits: list[dict], expected: str) -> tuple[bool, str]:
    if not hits:
        return False, "no hits"
    kind = _hit_kind(hits[0]) or "unknown"
    if kind == expected:
        return True, f"top-1 kind={kind}"
    return False, f"top-1 kind={kind} (want {expected})"


def _check_top1_text_any(hits: list[dict], needles: list[str]) -> tuple[bool, str]:
    if not hits:
        return False, "no hits"
    text = _hit_text(hits[0])
    if _text_matches_any(text, needles):
        return True, "top-1 matched expected text"
    preview = text[:60].replace("\n", " ")
    return False, f"top-1 text mismatch: {preview!r}"


def _check_forbid_top1_text(hits: list[dict], forbidden: list[str]) -> tuple[bool, str]:
    if not hits:
        return True, "no hits"
    text = _hit_text(hits[0])
    if _text_matches_any(text, forbidden):
        return False, "top-1 matched forbidden text"
    return True, "top-1 avoided forbidden text"


def _check_top_k_text_any(hits: list[dict], top_k: int, needles: list[str]) -> tuple[bool, str]:
    window = hits[:top_k]
    if not window:
        return False, "no hits"
    for hit in window:
        if _text_matches_any(_hit_text(hit), needles):
            return True, f"top-{top_k} contains expected marker"
    return False, f"top-{top_k} missing markers: {needles[:2]}..."


def _check_beats_baseline_title(
    baseline_hits: list[dict], swag_hits: list[dict]
) -> tuple[bool, str]:
    if not _baseline_top_is_bad(baseline_hits):
        return True, "baseline top-1 already acceptable"
    if not swag_hits:
        return False, "SWAG returned no hits"
    swag_kind = _hit_kind(swag_hits[0])
    if swag_kind == "body" and not _looks_like_title_chunk(_hit_text(swag_hits[0])):
        return True, "SWAG body beat baseline title/nav"
    return False, "SWAG did not beat baseline title/nav at rank 1"


def score_case(
    case: dict[str, Any],
    *,
    swag_hits: list[dict],
    baseline_hits: list[dict],
    top_k: int,
) -> dict[str, Any]:
    spec = case.get("swag") or {}
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    if "min_hits" in spec:
        ok, msg = _check_min_hits(swag_hits, int(spec["min_hits"]))
        checks["min_hits"] = ok
        details["min_hits"] = msg

    if spec.get("top1_kind"):
        ok, msg = _check_top1_kind(swag_hits, str(spec["top1_kind"]))
        checks["top1_kind"] = ok
        details["top1_kind"] = msg

    if spec.get("top1_text_any"):
        ok, msg = _check_top1_text_any(swag_hits, list(spec["top1_text_any"]))
        checks["top1_text_any"] = ok
        details["top1_text_any"] = msg

    if spec.get("forbid_top1_text_any"):
        ok, msg = _check_forbid_top1_text(swag_hits, list(spec["forbid_top1_text_any"]))
        checks["forbid_top1_text_any"] = ok
        details["forbid_top1_text_any"] = msg

    if spec.get("top_k_text_any"):
        ok, msg = _check_top_k_text_any(
            swag_hits, top_k, list(spec["top_k_text_any"])
        )
        checks["top_k_text_any"] = ok
        details["top_k_text_any"] = msg

    if spec.get("require_beats_baseline_title"):
        ok, msg = _check_beats_baseline_title(baseline_hits, swag_hits)
        checks["beats_baseline_title"] = ok
        details["beats_baseline_title"] = msg

    passed = bool(checks) and all(checks.values())
    return {
        "id": case.get("id"),
        "query": case.get("query"),
        "notes": case.get("notes"),
        "passed": passed,
        "checks": checks,
        "details": details,
        "top1_preview": (_hit_text(swag_hits[0])[:90].replace("\n", " ") if swag_hits else None),
    }


def summarize_eval(results: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for row in results if row.get("passed"))
    ratio = passed / total if total else 0.0
    verdict = "PASS" if ratio >= threshold else "FAIL"
    return {
        "verdict": verdict,
        "passed": passed,
        "total": total,
        "pass_ratio": round(ratio, 3),
        "pass_threshold": threshold,
    }


def format_eval_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "=== Query battery eval ===",
        (
            f"Verdict: {summary['verdict']} "
            f"({summary['passed']}/{summary['total']}, "
            f"threshold {summary['pass_threshold']:.0%})"
        ),
    ]
    for row in results:
        mark = "PASS" if row.get("passed") else "FAIL"
        cid = row.get("id") or row.get("query")
        detail_bits = [
            row.get("details", {}).get(key)
            for key in ("top1_kind", "beats_baseline_title", "top_k_text_any", "min_hits")
            if row.get("details", {}).get(key)
        ]
        suffix = "; ".join(detail_bits[:2]) if detail_bits else (row.get("notes") or "")
        lines.append(f"  {mark:<4} {cid:<28} {suffix}")
    return "\n".join(lines)
