from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from rag_agent.config import settings
from rag_agent.query_assess import assess_query, retrieval_confidence
from rag_agent.roses import bind_scenario, start_roses
from rag_agent.state import RAGState
from rag_agent.tools import (
    chunks_for_resume,
    generate_answer,
    merge_hits,
    retrieve_chunks,
    rewrite_query,
)


def assess_node(state: RAGState) -> dict:
    assessment = assess_query(state["question"])
    roses = start_roses(state["question"], state.get("topic"), assessment)
    return {"assessment": assessment, "roses": roses}


def retrieve_node(state: RAGState) -> dict:
    question = state["question"]
    topic = state.get("topic")
    top_k = state.get("top_k")
    search_kwargs = {
        "topic": topic,
        "top_k": top_k,
        "min_score": state.get("min_score"),
        "hnsw_ef": state.get("hnsw_ef"),
        "exact_search": bool(state.get("exact_search")),
    }
    chunks = retrieve_chunks(question, **search_kwargs)
    confidence = retrieval_confidence(chunks)
    rewritten = None
    if settings.rag_corrective and confidence.get("level") in {"empty", "low"}:
        rewritten = rewrite_query(question)
        if rewritten and rewritten.strip().lower() != question.strip().lower():
            second = retrieve_chunks(rewritten, **search_kwargs)
            if second:
                chunks = merge_hits(second, chunks) if chunks else second
                confidence = retrieval_confidence(chunks)
    roses = bind_scenario(state.get("roses") or {}, chunks, confidence)
    if rewritten and rewritten.strip().lower() != question.strip().lower():
        steps = list(roses.get("steps") or [])
        note = "Search again with a clearer phrasing."
        if note not in steps:
            steps.insert(2, note)
        roses["steps"] = steps
    return {"chunks": chunks, "confidence": confidence, "roses": roses}


def review_node(state: RAGState) -> dict:
    """Always pause after vector search so the UI shows corpus hits before Bedrock."""
    # Legacy override: hitl_enabled=False skips review (not recommended for demos).
    if state.get("hitl_enabled") is False:
        return {
            "approved_chunks": state.get("chunks") or [],
            "skip_llm": False,
            "allow_ungrounded": False,
        }

    chunks = state.get("chunks") or []
    confidence = state.get("confidence") or retrieval_confidence(chunks)
    assessment = state.get("assessment") or assess_query(state["question"])

    payload = {
        "type": "review_retrieval",
        "question": state["question"],
        "chunks": chunks,
        "assessment": assessment,
        "confidence": confidence,
        "roses": state.get("roses") or {},
        "message": "Review the passages below. Remove any you do not want used in the answer.",
        "actions": [
            {
                "id": "generate",
                "label": "Generate answer from selected chunks",
                "cost": "uses selected passages",
            },
            {
                "id": "ungrounded",
                "label": "Answer without corpus (not recommended)",
                "cost": "not grounded in your documents",
            },
            {
                "id": "abort",
                "label": "Stop — rephrase first (cheapest)",
                "cost": "no answer generated",
            },
        ],
    }
    decision = interrupt(payload)

    if isinstance(decision, dict):
        action = decision.get("action") or "generate"
        if action == "abort":
            return {
                "approved_chunks": [],
                "skip_llm": True,
                "answer": "Stopped. Rephrase the question and ask again.",
            }
        if action == "ungrounded":
            return {
                "approved_chunks": [],
                "allow_ungrounded": True,
                "skip_llm": False,
            }
        approved = chunks_for_resume(decision.get("chunks", decision.get("resume", chunks)))
        if not approved:
            approved = chunks
        return {
            "approved_chunks": approved,
            "allow_ungrounded": False,
            "skip_llm": False,
        }

    # Resume sent a bare chunk list (UI shortcut)
    approved = chunks_for_resume(decision)
    if not approved:
        approved = chunks
    return {"approved_chunks": approved, "skip_llm": False, "allow_ungrounded": False}


def generate_node(state: RAGState) -> dict:
    if state.get("skip_llm"):
        return {"answer": state.get("answer") or "Stopped."}

    chunks = state.get("approved_chunks") or state.get("chunks") or []
    allow_ungrounded = bool(state.get("allow_ungrounded"))

    if not chunks and not allow_ungrounded:
        return {
            "answer": "No passages selected. Choose sources or rephrase the question.",
        }

    answer = generate_answer(
        state["question"],
        chunks,
        ungrounded=allow_ungrounded and not chunks,
        roses=state.get("roses"),
    )
    return {"answer": answer}


def build_graph():
    g = StateGraph(RAGState)
    g.add_node("assess", assess_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("review", review_node)
    g.add_node("generate", generate_node)
    g.add_edge(START, "assess")
    g.add_edge("assess", "retrieve")
    g.add_edge("retrieve", "review")
    g.add_edge("review", "generate")
    g.add_edge("generate", END)
    return g


def get_checkpointer():
    if settings.use_dynamodb_checkpointer:
        from langgraph_checkpoint_aws import DynamoDBSaver

        return DynamoDBSaver(
            table_name=settings.checkpoint_table,
            region_name=settings.aws_region,
        )
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


graph = build_graph().compile(checkpointer=get_checkpointer())
