from rag_agent.query_assess import retrieval_confidence
from rag_agent.tools import merge_hits


def test_merge_hits_primary_wins():
    primary = [{"id": "a", "text": "first"}]
    extra = [{"id": "a", "text": "second"}, {"id": "b", "text": "other"}]
    merged = merge_hits(primary, extra)
    assert [h["id"] for h in merged] == ["a", "b"]
    assert merged[0]["text"] == "first"


def test_confidence_ok_after_rerank():
    result = retrieval_confidence([{"id": "1", "score": 0.05, "rerank_score": 0.8}])
    assert result["level"] == "ok"


def test_confidence_ok_for_phrase_match():
    result = retrieval_confidence([{"id": "1", "score": 0.0, "phrase_match": True}])
    assert result["level"] == "ok"


def test_confidence_low_for_weak_vector():
    result = retrieval_confidence([{"id": "1", "score": 0.05}])
    assert result["level"] == "low"
