from ingestion.config import settings
from ingestion.swag.gating import should_rerank_with_swag


def test_gating_never_skips_multi_candidate_pool():
    hits = [
        {"text": "Substantive RAG paragraph about retrieval augmented generation.", "score": 0.52},
        {"text": "Another body chunk with overlapping context.", "score": 0.48},
    ]
    gate, reason = should_rerank_with_swag(hits, query="what is rag")
    assert gate is True
    assert reason == "multi_candidate_filter"


def test_gating_still_reranks_when_top_is_misclassified_body(monkeypatch):
    monkeypatch.setattr(settings, "swag_adaptive_vector_epsilon", 0.03)
    hits = [
        {"text": "# Title only", "score": 0.55, "chunk_kind": "body"},
        {"text": "Real answer about RAG pipelines and chunking.", "score": 0.50},
    ]
    gate, reason = should_rerank_with_swag(hits, query="how does rag work")
    assert gate is True
    assert "multi_candidate" in reason
