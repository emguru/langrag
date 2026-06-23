"""Symmetric baseline and split SWAG lift metrics."""

from ingestion.augmentation import _finalize_retrieval_hits, _symmetric_baseline_hits
from ingestion.lift_metrics import compute_lift_metrics


def test_finalize_prunes_title_chunk():
    pool = [
        {
            "id": "title",
            "text": "# RAG From Scratch\n",
            "chunk_kind": "title",
            "score": 0.9,
            "document_id": "doc-1",
        },
        {
            "id": "body",
            "text": "Retrieval augmented generation combines search with LLMs.",
            "chunk_kind": "body",
            "score": 0.7,
            "document_id": "doc-1",
        },
    ]
    finalized, meta = _finalize_retrieval_hits(pool[:1], pool, top_k=3)
    assert finalized
    assert finalized[0]["id"] == "body"
    assert meta.get("prune", {}).get("kept_kind") == "body"


def test_symmetric_baseline_matches_stack_on_titles():
    raw = [
        {
            "id": "t",
            "text": "# Title\n",
            "chunk_kind": "title",
            "score": 0.8,
            "document_id": "doc-1",
        }
    ]
    pool = raw + [
        {
            "id": "b",
            "text": "Body about RAG systems.",
            "chunk_kind": "body",
            "score": 0.6,
            "document_id": "doc-1",
        }
    ]
    symmetric = _symmetric_baseline_hits(raw, pool, top_k=2)
    assert symmetric[0]["id"] == "b"


def test_split_lift_rerank_vs_full():
    raw = [
        {"id": "t", "text": "# Title\n", "score": 0.8},
        {"id": "b", "text": "Body about retrieval augmented generation.", "score": 0.6},
    ]
    reranked = [
        {"id": "b", "text": "Body about retrieval augmented generation.", "score": 0.75, "swag_score": 0.7},
        {"id": "t", "text": "# Title\n", "score": 0.5, "swag_score": 0.1},
    ]
    pool = raw
    symmetric = _symmetric_baseline_hits(raw, pool, top_k=2)
    full, _ = _finalize_retrieval_hits(reranked, pool, top_k=2)

    rerank_lift = compute_lift_metrics(raw, reranked, top_k=2, pipeline="swag_rerank")
    full_lift = compute_lift_metrics(symmetric, full, top_k=2, pipeline="swag_full")

    assert rerank_lift["top1_quality_fix_pct"] == 100.0
    assert full_lift["top1_quality_fix_pct"] == 0.0


def test_crag_web_fallback_skips_backfill_flag():
    from ingestion.augmentation import _crag_cleared_all_hits, _crag_kept_hits

    assert _crag_cleared_all_hits(
        {"enabled": True, "action": "web_fallback", "kept": 0, "retrieved": 8}
    )
    assert not _crag_cleared_all_hits(
        {"enabled": True, "action": "partial_rag", "kept": 3, "retrieved": 8}
    )
    assert _crag_kept_hits(
        {"enabled": True, "action": "partial_rag", "kept": 1, "retrieved": 3}
    )
    assert not _crag_kept_hits(None)


def test_finalize_skips_cross_doc_pool_backfill_when_disabled():
    kept = [
        {
            "id": "a1",
            "document_id": "doc-a",
            "chunk_index": 1,
            "text": "Body about retrieval augmented generation.",
            "chunk_kind": "body",
            "score": 0.4,
        }
    ]
    pool = kept + [
        {
            "id": "b1",
            "document_id": "doc-b",
            "chunk_index": 0,
            "text": "Unrelated high-scoring body from another document.",
            "chunk_kind": "body",
            "score": 0.9,
        }
    ]
    with_backfill, backfill_meta = _finalize_retrieval_hits(
        kept, pool, top_k=8, allow_pool_backfill=True
    )
    without_backfill, no_backfill_meta = _finalize_retrieval_hits(
        kept, pool, top_k=8, allow_pool_backfill=False
    )
    assert len(with_backfill) > len(without_backfill)
    assert all(h["document_id"] == "doc-a" for h in without_backfill)
    assert no_backfill_meta.get("backfill", {}).get("skipped") == "crag_kept_hits"
