from rag_agent.roses import bind_scenario, roses_system_prompt, start_roses


def test_start_roses_is_lambda_plan():
    plan = start_roses("What is RAG?", None, {"ambiguous": False})
    assert "Answer only from the retrieved document passages" in plan["role"]
    assert plan["objective"] == "What is RAG?"
    assert plan["scenario"]["status"] == "pending_retrieval"


def test_bind_scenario_empty_refuses():
    plan = start_roses("What is RAG?", "hf-wiki", {})
    bound = bind_scenario(plan, [], {"level": "empty"})
    assert bound["scenario"]["chunk_count"] == 0
    assert "invent" in bound["expected_solution"].lower()


def test_bind_scenario_uses_sources():
    plan = start_roses("What is RAG?", None, {})
    bound = bind_scenario(
        plan,
        [{"source": "s3://docs/rag.md", "text": "RAG retrieves documents then generates.", "score": 0.8}],
        {"level": "ok"},
    )
    assert bound["scenario"]["sources"] == ["s3://docs/rag.md"]
    assert "Cite" in bound["expected_solution"]
    prompt = roses_system_prompt(bound)
    assert "OBJECTIVE: What is RAG?" in prompt
    assert "s3://docs/rag.md" in prompt
