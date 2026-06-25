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


def test_stopword_have_does_not_rescue_recipe():
    assert not lexical_query_matches_chunk(
        "what do we have in the cafeteria",
        "Produce a shopping list considering what we already have at home.",
    )


def test_cafeteria_token_required_for_cafeteria_query():
    assert lexical_query_matches_chunk(
        "what do we have in the cafeteria",
        "Serverless restaurant management for cafeteria operations.",
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


def test_decide_low_relevance_ambiguous_only_web_fallback():
    from ingestion.crag import decide_low_relevance_outcome

    action, kept = decide_low_relevance_outcome(
        [],
        [{"text": "invoice", "relevance": "ambiguous"}, {"text": "menu", "relevance": "ambiguous"}],
        top_k=8,
        query="invoice menu",
    )
    assert action == "web_fallback"
    assert kept == []


def test_decide_low_relevance_lexical_rescue_partial_rag():
    from ingestion.crag import decide_low_relevance_outcome

    action, kept = decide_low_relevance_outcome(
        [],
        [{"text": "pancakes", "relevance": "ambiguous", "lexical_rescue": True}],
        top_k=8,
        query="pancackes",
    )
    assert action == "partial_rag"
    assert len(kept) == 1


def test_decide_low_relevance_keyword_ambiguous_partial_rag():
    from ingestion.crag import decide_low_relevance_outcome

    action, kept = decide_low_relevance_outcome(
        [],
        [
            {
                "text": "Apple and flour pancakes recipe",
                "relevance": "ambiguous",
                "seed_key": "adversarial/pancake-recipe.md",
            }
        ],
        top_k=8,
        query="pancakes",
    )
    assert action == "partial_rag"
    assert len(kept) == 1


def test_rag_acronym_matches_chunk():
    assert lexical_query_matches_chunk(
        "how does rag work",
        "Retrieval augmented generation (RAG) combines LLMs with a vector store.",
    )


def test_partial_rag_when_relevant_below_min():
    from ingestion.crag import _resolve_below_min_relevant

    relevant = [
        {"text": "RAG overview", "relevance": "relevant", "score": 0.8},
        {"text": "RAG chunk two", "relevance": "relevant", "score": 0.7},
        {"text": "RAG chunk three", "relevance": "relevant", "score": 0.6},
    ]
    action, kept = _resolve_below_min_relevant(
        relevant, [], top_k=8, query="retrieval augmented generation"
    )
    assert action == "partial_rag"
    assert len(kept) == 3


def test_decide_low_relevance_cross_domain_ambiguous_web_fallback():
    from ingestion.crag import decide_low_relevance_outcome

    action, kept = decide_low_relevance_outcome(
        [],
        [
            {
                "text": "Invoice processing automation",
                "relevance": "ambiguous",
                "seed_key": "ap-procurement/invoice-processing.md",
            },
            {
                "text": "Weekly cafeteria menu",
                "relevance": "ambiguous",
                "seed_key": "cafeteria-ops/daily-menu.md",
            },
        ],
        top_k=8,
        query="invoice cafeteria menu",
    )
    assert action == "web_fallback"
    assert kept == []
