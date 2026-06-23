from ingestion.config import settings
from ingestion.same_document_expansion import expand_same_document_siblings


def _hit(
    doc_id: str,
    chunk_index: int,
    text: str,
    *,
    hit_id: str | None = None,
) -> dict:
    return {
        "id": hit_id or f"{doc_id}:{chunk_index}",
        "document_id": doc_id,
        "chunk_index": chunk_index,
        "text": text,
    }


def test_expansion_adds_siblings_in_reading_order(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    doc = "f5bd1441"
    kept = [_hit(doc, 1, "# LangChain RAG\n\nRetrieval augmented generation works by...")]
    pool = [
        _hit(doc, 0, "# LangChain RAG"),
        kept[0],
        _hit(doc, 2, "![rag diagram](https://example.com/rag.png)"),
        _hit("94c8d556", 0, "Fabrications are not real RAG papers"),
    ]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert meta["enabled"] is True
    assert meta["siblings_added"] == 2
    assert len(expanded) == 3
    assert all(h["document_id"] == doc for h in expanded)
    assert [h["chunk_index"] for h in expanded] == [1, 0, 2]
    assert expanded[0]["id"] == kept[0]["id"]
    assert expanded[0].get("expanded_sibling") is not True
    assert expanded[1].get("expanded_sibling") is True
    assert expanded[2].get("expanded_sibling") is True


def test_expansion_skips_unrelated_documents(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    kept = [_hit("doc-a", 1, "Body paragraph about RAG pipelines.")]
    pool = [
        _hit("doc-a", 0, "# Title"),
        kept[0],
        _hit("doc-b", 0, "Different article entirely."),
    ]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert meta["siblings_added"] == 1
    assert len(expanded) == 2
    assert {h["document_id"] for h in expanded} == {"doc-a"}


def test_expansion_skips_empty_and_boilerplate_siblings(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    kept = [_hit("doc-a", 1, "Useful body text with enough sentences to classify as body.")]
    pool = [
        kept[0],
        _hit("doc-a", 0, ""),
        _hit("doc-a", 2, "<html><head><meta></head><body></body></html>"),
        _hit("doc-a", 3, "Another substantive paragraph about retrieval."),
    ]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert meta["siblings_added"] == 1
    assert len(expanded) == 2
    assert expanded[1]["chunk_index"] == 3


def test_expansion_respects_max_per_document(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    monkeypatch.setattr(settings, "rag_same_document_max_per_doc", 2)
    kept = [_hit("doc-a", 0, "First kept chunk with body text.")]
    pool = [
        kept[0],
        _hit("doc-a", 1, "Second sibling chunk."),
        _hit("doc-a", 2, "Third sibling chunk should not appear."),
    ]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert len(expanded) == 2
    assert meta["siblings_added"] == 1


def test_expansion_disabled_returns_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", False)
    kept = [_hit("doc-a", 1, "Only kept chunk.")]
    pool = kept + [_hit("doc-a", 0, "Sibling title")]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert expanded == kept
    assert meta["siblings_added"] == 0
