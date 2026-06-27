from ingestion.config import settings
from ingestion.swag.chunk_quality import classify_chunk_kind, chunk_quality_multiplier
from ingestion.swag.heuristic import heuristic_query_profile, is_evaluative_query
from ingestion.swag.postprocess import passes_noise_gate
from ingestion.swag.rerank import rerank_hits_with_swag


def test_evaluative_query_detected_without_question_mark():
    assert is_evaluative_query("is rag useful")


def test_evaluative_query_profile_has_intent_and_hints():
    profile = heuristic_query_profile("is rag useful")
    assert profile.user_intents
    assert any("retrieval augmented generation" in t for t in profile.direct_synonyms)
    assert profile.business_goals


def test_classify_chunk_kinds():
    assert classify_chunk_kind("# RAG From Scratch") == "title"
    assert classify_chunk_kind("<!DOCTYPE html><html><head>") == "boilerplate"
    assert (
        classify_chunk_kind(
            "RAG has emerged as a popular mechanism to expand an LLM knowledge base."
        )
        == "body"
    )


def test_classify_readme_chrome_as_nav():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    chrome = (root / "fixtures/corpus/validation-chrome/langchain-rag-chrome.md").read_text()
    py_chrome = (root / "fixtures/corpus/validation-chrome/python-readme-chrome.md").read_text()
    body = (root / "fixtures/corpus/validation-chrome/langchain-rag-body.md").read_text()
    assert classify_chunk_kind(chrome) == "nav"
    assert classify_chunk_kind(py_chrome) == "nav"
    assert classify_chunk_kind(body) == "body"


def test_noise_gate_drops_distant_low_swag_hits():
    top = 0.505
    assert not passes_noise_gate({"swag_score": 0.0, "vector_score": 0.10}, top_vector=top)


def test_noise_gate_keeps_leader_band_or_semantic_match():
    top = 0.505
    assert passes_noise_gate({"swag_score": 0.15, "vector_score": 0.396}, top_vector=top)
    assert passes_noise_gate({"swag_score": 0.0, "vector_score": 0.40}, top_vector=top)


def test_noise_gate_drops_chrome_kinds_without_swag_floor():
    top = 0.505
    assert not passes_noise_gate(
        {"swag_score": 0.0, "vector_score": 0.50, "chunk_kind": "nav"},
        top_vector=top,
    )
    assert not passes_noise_gate(
        {"swag_score": 0.0, "vector_score": 0.50, "chunk_kind": "boilerplate"},
        top_vector=top,
    )


def test_is_rag_useful_rerank_promotes_substance(monkeypatch):
    monkeypatch.setattr(settings, "swag_persist_profiles", False)
    monkeypatch.setattr(settings, "swag_profile_mode", "heuristic")

    rag_paragraph = (
        "RAG has emerged as a popular and powerful mechanism to expand an LLM's "
        "knowledge base, using documents retrieved from an external data source "
        "to ground the LLM generation via in-context learning."
    )
    hits = [
        {
            "id": "title",
            "document_id": "rag-readme",
            "source": "s3://bucket/rag-demo/readme/README.md",
            "text": "# RAG From Scratch",
            "score": 0.505,
        },
        {
            "id": "body",
            "document_id": "rag-readme",
            "source": "s3://bucket/rag-demo/readme/README.md",
            "text": rag_paragraph,
            "score": 0.396,
        },
        {
            "id": "nav",
            "document_id": "rag-readme",
            "source": "s3://bucket/rag-demo/readme/README.md",
            "text": "![rag_detail_v2](https://github.com/langchain-ai/rag-from-scratch/assets/x) [Video playlist](https://youtube.com/playlist)",
            "score": 0.323,
        },
        {
            "id": "html",
            "document_id": "aws-patterns",
            "source": "s3://bucket/rag-demo/patterns/index",
            "text": "<!DOCTYPE html><html><head><meta charset=UTF-8><title>AWS Prescriptive Guidance</title>",
            "score": 0.102,
        },
        {
            "id": "prompt",
            "document_id": "prompt-doc",
            "source": "s3://bucket/rag-demo/prompt/README.md",
            "text": "You should see an output like the following: ```output very unhappy _____. Prompt templates for demos.",
            "score": 0.100,
        },
    ]

    reranked, meta = rerank_hits_with_swag("is rag useful", hits, top_k=5)

    assert meta["evaluative_query"] is True
    assert meta["noise_dropped"] >= 1
    assert rag_paragraph[:40] in reranked[0]["text"]
    assert all("<!DOCTYPE" not in h["text"] for h in reranked)
    assert classify_chunk_kind(reranked[0]["text"]) == "body"
    assert chunk_quality_multiplier("title", evaluative_query=True) < chunk_quality_multiplier(
        "body", evaluative_query=True
    )
