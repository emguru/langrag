"""Answer correctness, citation, and hallucination scoring for harness qrels."""

from __future__ import annotations

import re
from typing import Any

from validation_eval import hit_is_relevant, hit_seed_key

from eval_harness.metrics import _norm, _text_contains_any

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_STOP = frozenset(
    "a an the and or but if in on at to for of is are was were be been with from".split()
)


def _significant_tokens(text: str) -> list[str]:
    return [
        t.lower()
        for t in _WORD_RE.findall(text or "")
        if len(t) >= 3 and t.lower() not in _STOP
    ]


def answer_correctness(answer: str, case: dict[str, Any]) -> float:
    """0-1 score vs expected_answer and acceptable_alternate_terms."""
    if case.get("expect_no_answer"):
        lowered = _norm(answer)
        if not lowered or "insufficient evidence" in lowered:
            return 1.0
        return 0.0

    expected = str(case.get("expected_answer") or "").strip()
    if not expected:
        return 1.0

    lowered = _norm(answer)
    if _norm(expected) in lowered:
        return 1.0

    for alt in case.get("acceptable_alternate_terms") or []:
        alt_text = str(alt or "").strip()
        if alt_text and _norm(alt_text) in lowered:
            return 1.0

    exp_tokens = _significant_tokens(expected)
    if not exp_tokens:
        return 0.0
    ans_text = lowered
    hits = sum(1 for token in exp_tokens if token in ans_text)
    ratio = hits / len(exp_tokens)

    markers = list(case.get("gold_source_chunk_contains") or [])
    if markers and _text_contains_any(answer, markers):
        ratio = max(ratio, 0.75)

    return min(1.0, ratio)


def answer_citation_correct(
    answer: str,
    case: dict[str, Any],
    *,
    cited_seed_key: str | None,
    hits: list[dict],
) -> float:
    """Answer cites the gold document (via retrieval cite or chunk markers in text)."""
    if case.get("expect_no_answer"):
        return 1.0 if not hits else 0.0

    relevant = list(case.get("relevant_seed_keys") or [])
    gold = case.get("gold_source_document")
    if gold and gold not in relevant:
        relevant.append(gold)

    markers = list(case.get("gold_source_chunk_contains") or [])
    if markers and _text_contains_any(answer, markers):
        return 1.0

    if cited_seed_key and relevant:
        if cited_seed_key in relevant:
            if markers:
                top = hits[0] if hits else {}
                if _text_contains_any(str(top.get("text") or ""), markers):
                    return 1.0
            else:
                return 1.0

    if hits and relevant and hit_is_relevant(hits[0], relevant):
        if not markers or _text_contains_any(str(hits[0].get("text") or ""), markers):
            return 1.0

    return 0.0


def hallucination_rate(
    answer: str,
    case: dict[str, Any],
    *,
    cited_seed_key: str | None,
) -> float:
    """1.0 when answer authority comes from trap/outdated docs."""
    if case.get("expect_no_answer"):
        return 0.0 if not answer.strip() or "insufficient" in answer.lower() else 1.0

    traps = set(case.get("trap_documents") or []) | set(case.get("forbidden_top1_seed_keys") or [])
    if cited_seed_key and cited_seed_key in traps:
        return 1.0

    for trap in traps:
        if trap and trap.split("/")[-1].replace(".md", "") in _norm(answer):
            return 1.0
    return 0.0


def score_answer(
    answer: str,
    case: dict[str, Any],
    *,
    hits: list[dict],
    generation_meta: dict[str, Any],
) -> dict[str, float]:
    cited = generation_meta.get("cited_seed_key")
    correctness = answer_correctness(answer, case)
    citation = answer_citation_correct(
        answer, case, cited_seed_key=cited, hits=hits
    )
    hallucination = hallucination_rate(answer, case, cited_seed_key=cited)
    grounded = (
        1.0
        if correctness >= 0.6 and citation >= 1.0 and hallucination <= 0.0
        else 0.0
    )
    return {
        "answer_correctness": correctness,
        "answer_citation_correct": citation,
        "hallucination_rate": hallucination,
        "grounded_answer_rate": grounded,
    }


def check_answer_thresholds(
    report: dict[str, Any],
    *,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    method = str(thresholds.get("method") or "swag")
    baseline = str(thresholds.get("compare_baseline") or "hybrid_rrf")
    min_correct = float(thresholds.get("min_correctness") or 0.70)
    min_grounded = float(thresholds.get("min_grounded_rate") or 0.65)
    max_halluc = float(thresholds.get("max_hallucination_rate") or 0.10)

    generation = report.get("generation") or {}
    global_gen = generation.get("global") or {}
    swag = (global_gen.get(method) or {})
    base = (global_gen.get(baseline) or {})

    swag_correct = float(swag.get("answer_correctness") or 0.0)
    swag_grounded = float(swag.get("grounded_answer_rate") or 0.0)
    swag_halluc = float(swag.get("hallucination_rate") or 1.0)
    base_correct = float(base.get("answer_correctness") or 0.0)

    correct_ok = swag_correct >= min_correct
    grounded_ok = swag_grounded >= min_grounded
    halluc_ok = swag_halluc <= max_halluc
    beats_baseline = swag_correct >= base_correct

    verdict = correct_ok and grounded_ok and halluc_ok and beats_baseline
    lines = [
        "=== Answer behavior thresholds ===",
        f"Method {method}: correctness={swag_correct:.2f} (min {min_correct:.2f}) — {'PASS' if correct_ok else 'FAIL'}",
        f"Grounded rate={swag_grounded:.2f} (min {min_grounded:.2f}) — {'PASS' if grounded_ok else 'FAIL'}",
        f"Hallucination rate={swag_halluc:.2f} (max {max_halluc:.2f}) — {'PASS' if halluc_ok else 'FAIL'}",
        f"Correctness vs {baseline}: {swag_correct:.2f} vs {base_correct:.2f} — {'PASS' if beats_baseline else 'FAIL'}",
        f"Verdict: {'ANSWER SPEC PASS' if verdict else 'ANSWER SPEC FAIL'}",
    ]
    return {
        "verdict": "PASS" if verdict else "FAIL",
        "method": method,
        "compare_baseline": baseline,
        "metrics": swag,
        "baseline_metrics": base,
        "report_text": "\n".join(lines),
    }
