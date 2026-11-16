from ingestion.hybrid import lexical_score, merge_unique, rank_by_lexical, rrf_merge


def test_lexical_score_counts_overlap():
    assert lexical_score("retrieval augmented generation", "retrieval augmented generation") == 1.0
    assert lexical_score("chunking strategy", "unrelated legal clause") == 0.0


def test_rrf_merge_prefers_items_high_in_both_lists():
    dense = [{"id": "a", "text": "dense a"}, {"id": "b", "text": "dense b"}]
    lex = [{"id": "b", "text": "lex b"}, {"id": "c", "text": "lex c"}]
    merged = rrf_merge(dense, lex)
    assert [h["id"] for h in merged] == ["b", "a", "c"]
    assert merged[0]["rrf_score"] > merged[1]["rrf_score"]


def test_merge_unique_keeps_first_payload():
    first = [{"id": "a", "text": "one"}]
    second = [{"id": "a", "text": "two"}, {"id": "b", "text": "other"}]
    merged = merge_unique(first, second)
    assert [h["id"] for h in merged] == ["a", "b"]
    assert merged[0]["text"] == "one"


def test_rank_by_lexical_orders_hits():
    hits = [
        {"id": "weak", "text": "something else entirely"},
        {"id": "strong", "text": "langgraph human in the loop notes"},
    ]
    ranked = rank_by_lexical("langgraph human in the loop", hits)
    assert ranked[0]["id"] == "strong"
    assert ranked[0]["lexical_score"] > ranked[1]["lexical_score"]
