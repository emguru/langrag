from ingestion.config import settings
from ingestion.same_document_expansion import (
    expand_same_document_siblings,
    prune_low_value_hits,
)


def _hit(
    doc_id: str,
    chunk_index: int,
    text: str,
    *,
    hit_id: str | None = None,
    chunk_kind: str | None = None,
) -> dict:
    payload = {
        "id": hit_id or f"{doc_id}:{chunk_index}",
        "document_id": doc_id,
        "chunk_index": chunk_index,
        "text": text,
    }
    if chunk_kind:
        payload["chunk_kind"] = chunk_kind
    return payload


def test_expansion_adds_body_siblings_only(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    doc = "f5bd1441"
    kept = [
        _hit(
            doc,
            1,
            "Retrieval augmented generation works by combining search with generation.",
            chunk_kind="body",
        )
    ]
    pool = [
        _hit(doc, 0, "# LangChain RAG", chunk_kind="title"),
        kept[0],
        _hit(doc, 2, "![rag diagram](https://example.com/rag.png)", chunk_kind="nav"),
        _hit(
            doc,
            3,
            "Additional body paragraph explaining indexing and chunking for RAG.",
            chunk_kind="body",
        ),
        _hit("94c8d556", 0, "Fabrications are not real RAG papers", chunk_kind="body"),
    ]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert meta["siblings_added"] == 1
    assert len(expanded) == 2
    assert all(h["document_id"] == doc for h in expanded)
    assert expanded[0]["chunk_index"] == 1
    assert expanded[1]["chunk_index"] == 3
    assert expanded[1].get("expanded_sibling") is True


def test_expansion_skips_title_and_nav_siblings(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    kept = [_hit("doc-a", 1, "Body paragraph about RAG pipelines.", chunk_kind="body")]
    pool = [
        _hit("doc-a", 0, "# Title", chunk_kind="title"),
        kept[0],
        _hit("doc-b", 0, "Different article entirely.", chunk_kind="body"),
    ]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert meta["siblings_added"] == 0
    assert len(expanded) == 1


def test_expansion_respects_top_k_cap(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", True)
    kept = [_hit("doc-a", 0, "Primary body chunk with enough text.", chunk_kind="body")]
    pool = [
        kept[0],
        _hit("doc-a", 1, "Second body chunk with substantive RAG explanation.", chunk_kind="body"),
        _hit("doc-a", 2, "Third body chunk with more retrieval context.", chunk_kind="body"),
    ]

    expanded, _ = expand_same_document_siblings(kept, pool, top_k=2)

    assert len(expanded) == 2


def test_prune_drops_title_and_nav(monkeypatch):
    monkeypatch.setattr(settings, "rag_filter_non_body_hits", True)
    hits = [
        _hit("d", 0, "# Title only", chunk_kind="title"),
        _hit("d", 1, "Real explanatory body about retrieval augmented generation.", chunk_kind="body"),
        _hit("d", 2, "![img](http://x)", chunk_kind="nav"),
    ]

    pruned, meta = prune_low_value_hits(hits, top_k=8)

    assert meta["filtered"] == 2
    assert len(pruned) == 1
    assert pruned[0]["chunk_kind"] == "body"


def test_expansion_disabled_returns_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "rag_expand_same_document", False)
    kept = [_hit("doc-a", 1, "Only kept chunk.")]
    pool = kept + [_hit("doc-a", 0, "Sibling title")]

    expanded, meta = expand_same_document_siblings(kept, pool, top_k=8)

    assert expanded == kept
    assert meta["siblings_added"] == 0
