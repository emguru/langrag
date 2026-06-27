"""Unit tests for SWAG eval harness metrics (no embedder)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

from eval_harness.metrics import (
    citation_correct_at_1,
    ndcg_at_k,
    recall_at_k,
    score_retrieval,
    wrong_version_rate,
)


def _hit(seed_key: str, text: str = "body") -> dict:
    return {"seed_key": seed_key, "text": text, "score": 0.5}


def test_recall_and_ndcg():
    hits = [
        _hit("gold/doc.md", "Brannock device sizing"),
        _hit("trap/old.md", "deprecated"),
    ]
    relevant = ["gold/doc.md"]
    assert recall_at_k(hits, relevant, 5) == 1.0
    assert ndcg_at_k(hits, relevant, 5) > 0.5


def test_wrong_version_rate():
    hits = [
        _hit("trap/old.md"),
        _hit("gold/doc.md"),
    ]
    case = {"trap_documents": ["trap/old.md"]}
    assert wrong_version_rate(hits, case, k=2) == 0.5


def test_citation_correct_requires_chunk_marker():
    case = {
        "relevant_seed_keys": ["gold/doc.md"],
        "gold_source_document": "gold/doc.md",
        "gold_source_chunk_contains": ["Brannock"],
    }
    good = [_hit("gold/doc.md", "Use Brannock device")]
    bad = [_hit("gold/doc.md", "wrong chunk text")]
    assert citation_correct_at_1(good, case) == 1.0
    assert citation_correct_at_1(bad, case) == 0.0


def test_score_retrieval_bundle():
    case = {
        "relevant_seed_keys": ["gold/doc.md"],
        "gold_source_document": "gold/doc.md",
        "gold_source_chunk_contains": ["answer phrase"],
        "trap_documents": ["trap/old.md"],
    }
    hits = [_hit("gold/doc.md", "answer phrase here")]
    metrics = score_retrieval(hits, case, k=5)
    assert metrics["recall_at_k"] == 1.0
    assert metrics["citation_correct_at_1"] == 1.0
