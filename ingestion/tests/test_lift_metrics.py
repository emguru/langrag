from ingestion.lift_metrics import compute_lift_metrics


def test_lift_rank_and_quality_fix():
    baseline = [
        {"text": "# RAG From Scratch\n", "score": 0.8, "id": "a"},
        {"text": "Body about retrieval augmented generation.", "score": 0.6, "id": "b"},
    ]
    augmented = [
        {"text": "Body about retrieval augmented generation.", "score": 0.75, "id": "b", "swag_score": 0.7},
        {"text": "# RAG From Scratch\n", "score": 0.5, "id": "a", "swag_score": 0.1},
    ]
    lift = compute_lift_metrics(baseline, augmented, top_k=2, pipeline="swag")
    assert lift["rank_improved_pct"] > 0
    assert lift["top1_quality_fix_pct"] == 100.0
    assert lift["has_positive_lift"] is True


def test_crag_noise_filtered():
    baseline = [{"text": f"chunk {i}", "score": 0.5, "id": str(i)} for i in range(8)]
    augmented = baseline[:3]
    lift = compute_lift_metrics(
        baseline,
        augmented,
        top_k=8,
        pipeline="crag",
        extra={"retrieved": 8, "kept": 3},
    )
    assert lift["noise_filtered_pct"] == 62.5
    assert lift["relevant_kept_pct"] == 37.5
