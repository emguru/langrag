from __future__ import annotations

import json
from typing import Any

import httpx
from langchain_core.tools import tool

from research_team.config import settings


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed texts using configured provider."""
    if settings.llm_provider == "openai" and settings.openai_api_key:
        from langchain_openai import OpenAIEmbeddings

        embedder = OpenAIEmbeddings(model="text-embedding-3-small")
        return embedder.embed_documents(texts)

    from langchain_aws import BedrockEmbeddings

    embedder = BedrockEmbeddings(
        model_id=settings.default_embedding_model,
        region_name=settings.bedrock_region,
    )
    return embedder.embed_documents(texts)


def get_llm():
    """Return configured chat model."""
    if settings.llm_provider == "openai" and settings.openai_api_key:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o", temperature=0.2)

    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=settings.default_llm_model,
        region_name=settings.bedrock_region,
        temperature=0.2,
        timeout=settings.bedrock_read_timeout,
    )


def _web_search_results(query: str) -> list[dict[str, Any]]:
    if not settings.tavily_api_key:
        return []
    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query=query, max_results=5)
    results = response.get("results", [])
    return results if isinstance(results, list) else []


def _format_web_hits(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        hits.append(
            {
                "score": None,
                "text": item.get("content") or item.get("snippet") or "",
                "source": item.get("url") or item.get("title") or "web",
                "page": None,
                "origin": "web",
            }
        )
    return hits


@tool
def rag_search(query: str, collection: str = "") -> str:
    """Search uploaded documents using SWAG reranking and/or Corrective RAG."""
    collection = collection or settings.qdrant_collection
    if settings.swag_enabled and settings.crag_enabled:
        mode = "swag_crag"
    elif settings.swag_enabled:
        mode = "swag"
    elif settings.crag_enabled:
        mode = "crag"
    else:
        mode = "baseline"
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{settings.ingestion_api_url}/rag/search",
                json={
                    "query": query,
                    "collection": collection,
                    "top_k": settings.rag_top_k,
                    "mode": mode,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return f"RAG search unavailable: {exc}"

    hits = data.get("hits") or []
    crag = data.get("crag") or {}
    swag = data.get("swag") or {}
    action = crag.get("action")

    if action == "web_fallback" or (not hits and settings.crag_enabled):
        web_hits = _format_web_hits(_web_search_results(query))
        if web_hits:
            return json.dumps(
                {
                    "hits": web_hits,
                    "crag": {**crag, "web_fallback_used": True},
                },
                indent=2,
            )
        if not hits:
            return "No relevant documents found; web fallback unavailable."

    if not hits:
        return "No relevant documents found in the vector store."

    payload = {"hits": hits, "mode": data.get("mode", mode)}
    if crag:
        payload["crag"] = crag
    if swag:
        payload["swag"] = swag
    return json.dumps(payload, indent=2)


@tool
def web_search(query: str) -> str:
    """Search the web for current information on a topic."""
    if not settings.tavily_api_key:
        return "Web search unavailable: TAVILY_API_KEY not configured."

    results = _web_search_results(query)
    return json.dumps(results, indent=2)


@tool
def trigger_ingestion(source_url: str, topic: str, collection: str = "") -> str:
    """Trigger ingestion of a document URL into the RAG vector store."""
    collection = collection or settings.qdrant_collection
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{settings.ingestion_api_url}/ingest/url",
                json={"url": source_url, "topic": topic, "collection": collection},
            )
            resp.raise_for_status()
            return json.dumps(resp.json())
    except Exception as exc:
        return f"Ingestion request failed: {exc}"


RESEARCH_TOOLS = [rag_search, web_search, trigger_ingestion]
