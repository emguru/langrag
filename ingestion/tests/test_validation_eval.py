"""Unit tests for qrel validation (no cluster)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

from validation_eval import (
    evaluate_case,
    hit_is_relevant,
    mrr,
    precision_at_1,
    summarize_results,
)


def _hit(seed_key: str, text: str = "body") -> dict:
    return {"seed_key": seed_key, "text": text, "score": 0.5}


def test_hit_is_relevant_by_seed_key():
    hit = _hit("ap-procurement/three-way-matching.md", "Three-way matching compares PO")
    assert hit_is_relevant(hit, ["ap-procurement/three-way-matching.md"])
    assert not hit_is_relevant(hit, ["other/doc.md"])


def test_precision_and_mrr():
    hits = [
        _hit("noise/doc.md"),
        _hit("rag-demo/langchain-rag-intro.md", "LLMs are trained on a large but fixed corpus"),
    ]
    assert precision_at_1(hits, ["rag-demo/langchain-rag-intro.md"]) == 0.0
    assert mrr(hits, ["rag-demo/langchain-rag-intro.md"]) == 0.5


def test_evaluate_case_top1_content():
    case = {
        "id": "t",
        "query": "q",
        "relevant_seed_keys": ["rag-demo/langchain-rag-intro.md"],
        "top1_must_contain": ["LLMs are trained"],
        "forbidden_top1_contains": ["Table of Contents"],
    }
    response = {
        "hits": [_hit("rag-demo/langchain-rag-intro.md", "LLMs are trained on a large but fixed corpus")],
        "baseline_hits": [],
        "baseline_hits_raw": [],
    }
    row = evaluate_case(case, response, top_k=8)
    assert row["passed"] is True


def test_evaluate_case_no_hits():
    case = {"id": "off", "expect_hits": False}
    row = evaluate_case(case, {"hits": []}, top_k=8)
    assert row["passed"] is True


def test_summarize_results_threshold():
    sheet = {"pass_threshold": 0.8, "description": "test"}
    results = [
        {"id": "a", "passed": True, "checks": {"x": True}, "details": {}},
        {"id": "b", "passed": False, "checks": {"x": False}, "details": {}},
    ]
    summary = summarize_results(sheet, results)
    assert summary["verdict"] == "FAIL"
    assert summary["passed"] == 1
