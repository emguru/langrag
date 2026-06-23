from ingestion.confidence import apply_vector_confidence_floor, top_vector_score


def test_floor_blocks_off_topic_scores():
    hits = [{"score": 0.059, "text": "irrelevant"}]
    gated, meta = apply_vector_confidence_floor(hits, threshold=0.12)
    assert gated == []
    assert meta is not None
    assert meta["low_confidence"] is True
    assert meta["top_vector_score"] == 0.059


def test_floor_passes_in_corpus_scores():
    hits = [{"score": 0.580, "text": "RAG body"}]
    gated, meta = apply_vector_confidence_floor(hits, threshold=0.12)
    assert len(gated) == 1
    assert meta is None


def test_floor_disabled_when_zero():
    hits = [{"score": 0.01, "text": "x"}]
    gated, meta = apply_vector_confidence_floor(hits, threshold=0.0)
    assert len(gated) == 1
    assert meta is None


def test_top_vector_score_empty():
    assert top_vector_score([]) == 0.0
