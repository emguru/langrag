"""AWS Lambda handler for the clean RAG LangGraph."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

# LangSmith — must be sync in Lambda or traces never flush before freeze.
if os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_CALLBACKS_BACKGROUND"] = "false"
    # Older LangChain still reads LANGCHAIN_API_KEY
    os.environ.setdefault("LANGCHAIN_API_KEY", os.environ["LANGSMITH_API_KEY"])
    project = os.getenv("LANGSMITH_PROJECT") or "rag-demo"
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ["LANGCHAIN_PROJECT"] = project

from langgraph.types import Command

from rag_agent.graph import graph


def _flush_langsmith() -> None:
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception:
        try:
            from langsmith import Client

            Client().flush()
        except Exception:
            pass


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def _config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "rag_ask",
        "tags": ["clean-rag", "lambda", "roses"],
        "metadata": {"thread_id": thread_id},
    }


def handler(event: dict, context: Any) -> dict:
    path = event.get("rawPath") or event.get("path") or ""
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "POST"
    )

    if method == "GET" and path.endswith("/health"):
        return _response(
            200,
            {
                "status": "ok",
                "methodology": "roses",
                "runtime": "lambda",
                "langsmith_tracing": bool(os.getenv("LANGSMITH_API_KEY")),
                "langsmith_project": os.getenv("LANGSMITH_PROJECT") or None,
            },
        )

    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    try:
        if path.endswith("/resume"):
            thread_id = payload.get("thread_id") or str(uuid.uuid4())
            resume_value = payload.get("resume")
            if resume_value is None:
                return _response(400, {"error": "resume is required"})
            result = graph.invoke(
                Command(resume=resume_value), config=_config(thread_id)
            )
            return _format_result(thread_id, result)

        if path.endswith("/runs") or path.endswith("/ask"):
            thread_id = payload.get("thread_id") or str(uuid.uuid4())
            question = payload.get("question") or payload.get("query")
            if not question:
                return _response(400, {"error": "question is required"})
            state = {
                "question": question,
                "topic": payload.get("topic"),
                "hitl_enabled": payload.get("hitl_enabled", True),
                "top_k": payload.get("top_k"),
                "min_score": payload.get("min_score"),
                "hnsw_ef": payload.get("hnsw_ef"),
                "exact_search": payload.get("exact_search"),
            }
            result = graph.invoke(state, config=_config(thread_id))
            return _format_result(thread_id, result)

        return _response(404, {"error": f"Unknown route: {method} {path}"})
    finally:
        _flush_langsmith()


def _format_result(thread_id: str, result: dict) -> dict:
    interrupts = result.get("__interrupt__")
    if interrupts:
        first = interrupts[0]
        value = getattr(first, "value", first)
        return _response(
            200,
            {
                "thread_id": thread_id,
                "status": "interrupted",
                "interrupt": value,
                "roses": (value or {}).get("roses") if isinstance(value, dict) else None,
            },
        )
    return _response(
        200,
        {
            "thread_id": thread_id,
            "status": "completed",
            "answer": result.get("answer"),
            "chunks": result.get("approved_chunks") or result.get("chunks"),
            "roses": result.get("roses"),
        },
    )


if __name__ == "__main__":
    os.environ.setdefault("USE_DYNAMODB_CHECKPOINTER", "false")
    demo = handler(
        {
            "rawPath": "/ask",
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"question": "What is RAG?", "hitl_enabled": False}),
        },
        None,
    )
    print(demo)
