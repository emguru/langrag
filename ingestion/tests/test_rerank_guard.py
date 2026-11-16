from ingestion.rerank import (
    AMAZON_RERANK_MODEL_ID,
    amazon_rerank,
    rerank_model_allowed,
    resolved_rerank_model,
)


def test_only_amazon_rerank_v1_is_allowed():
    assert rerank_model_allowed(AMAZON_RERANK_MODEL_ID) is True
    assert rerank_model_allowed("cohere.rerank-v3-5:0") is False
    assert rerank_model_allowed("cohere.rerank-v3-5") is False
    assert rerank_model_allowed("opensearch") is False
    assert rerank_model_allowed("") is False


def test_resolved_model_rejects_cohere(monkeypatch):
    from ingestion import rerank as rerank_mod

    monkeypatch.setattr(rerank_mod.settings, "rerank_model_id", "cohere.rerank-v3-5:0")
    assert resolved_rerank_model() is None


def test_amazon_rerank_skips_when_disabled(monkeypatch):
    from ingestion import rerank as rerank_mod

    monkeypatch.setattr(rerank_mod.settings, "rag_rerank", False)
    assert amazon_rerank("what is rag", [{"id": "1", "text": "rag"}], top_n=1) is None


def test_amazon_rerank_skips_forbidden_model(monkeypatch):
    from ingestion import rerank as rerank_mod

    monkeypatch.setattr(rerank_mod.settings, "rag_rerank", True)
    monkeypatch.setattr(rerank_mod.settings, "rerank_model_id", "cohere.rerank-v3-5:0")
    assert amazon_rerank("what is rag", [{"id": "1", "text": "rag"}], top_n=1) is None
