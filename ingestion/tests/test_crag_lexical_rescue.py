from ingestion.crag import _apply_lexical_rescue, lexical_query_matches_chunk


def test_typo_matches_indexed_word():
    assert lexical_query_matches_chunk(
        "pancackes",
        "-Apple and flour pancakes: 1 cup flour, 1/2 tsp baking powder",
    )


def test_exact_keyword_match():
    assert lexical_query_matches_chunk("pancakes", "Apple and flour pancakes recipe")


def test_unrelated_query_does_not_match():
    assert not lexical_query_matches_chunk(
        "shoe sizing",
        "-Apple and flour pancakes: 1 cup flour",
    )


def test_rescue_upgrades_irrelevant_only_when_all_cleared():
    graded = [
        {
            "text": "-Apple and flour pancakes: 1 cup flour",
            "relevance": "irrelevant",
            "score": 0.13,
        },
        {"text": "LangChain forum discussions", "relevance": "irrelevant", "score": 0.10},
    ]
    rescued = _apply_lexical_rescue("pancackes", graded)
    assert rescued[0]["relevance"] == "ambiguous"
    assert rescued[0].get("lexical_rescue") is True
    assert rescued[1]["relevance"] == "irrelevant"


def test_rescue_skipped_when_relevant_exists():
    graded = [
        {"text": "pancakes", "relevance": "relevant", "score": 0.8},
        {"text": "other", "relevance": "irrelevant", "score": 0.1},
    ]
    assert _apply_lexical_rescue("pancackes", graded) == graded
