"""Unit tests for harness answer generation and scoring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

from eval_harness.answer_metrics import (
    answer_correctness,
    answer_citation_correct,
    check_answer_thresholds,
    hallucination_rate,
    score_answer,
)
from eval_harness.generation import extractive_answer


def _case(**kwargs):
    base = {
        "expected_answer": "45 days with receipt for unworn items.",
        "gold_source_document": "eval-harness/retail-furniture/return-policy-2024.md",
        "gold_source_chunk_contains": ["45 days"],
        "relevant_seed_keys": ["eval-harness/retail-furniture/return-policy-2024.md"],
        "trap_documents": ["eval-harness/retail-furniture/duplicate-return-policy-copy.md"],
        "acceptable_alternate_terms": ["45-day return window"],
    }
    base.update(kwargs)
    return base


def _hit(seed_key: str, text: str) -> dict:
    return {"seed_key": seed_key, "text": text, "score": 0.8}


def test_answer_correctness_expected_and_alternate():
    case = _case()
    assert answer_correctness("Returns accepted within 45 days with receipt.", case) >= 0.75
    assert answer_correctness("45-day return window applies.", case) == 1.0


def test_answer_citation_requires_gold_doc():
    case = _case()
    hits = [_hit(case["gold_source_document"], "Unworn footwear may be returned within 45 days.")]
    ok = answer_citation_correct(
        "45 days with receipt",
        case,
        cited_seed_key=case["gold_source_document"],
        hits=hits,
    )
    assert ok == 1.0


def test_hallucination_when_citing_trap():
    case = _case(
        forbidden_top1_seed_keys=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"],
        trap_documents=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"],
    )
    assert (
        hallucination_rate(
            "Use deprecated sizing guidance.",
            case,
            cited_seed_key="eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md",
        )
        == 1.0
    )


def test_extractive_answer_from_top_hit():
    hits = [
        _hit(
            "eval-harness/retail-furniture/return-policy-2024.md",
            "Unworn footwear may be returned within 45 days with receipt.",
        )
    ]
    answer, meta = extractive_answer("furniture return window 2024", hits)
    assert "45 days" in answer
    assert meta["cited_seed_key"] == hits[0]["seed_key"]


def test_score_answer_bundle():
    case = _case()
    hits = [_hit(case["gold_source_document"], "Return within 45 days with receipt.")]
    answer, meta = extractive_answer("return window", hits)
    metrics = score_answer(answer, case, hits=hits, generation_meta=meta)
    assert metrics["answer_correctness"] >= 0.6
    assert metrics["grounded_answer_rate"] == 1.0


def test_check_answer_thresholds_passes_good_report():
    report = {
        "generation": {
            "global": {
                "swag": {
                    "answer_correctness": 0.82,
                    "grounded_answer_rate": 0.75,
                    "hallucination_rate": 0.05,
                },
                "hybrid_rrf": {
                    "answer_correctness": 0.70,
                    "grounded_answer_rate": 0.60,
                    "hallucination_rate": 0.08,
                },
            }
        }
    }
    result = check_answer_thresholds(
        report,
        thresholds={
            "method": "swag",
            "compare_baseline": "hybrid_rrf",
            "min_correctness": 0.70,
            "min_grounded_rate": 0.65,
            "max_hallucination_rate": 0.10,
        },
    )
    assert result["verdict"] == "PASS"
