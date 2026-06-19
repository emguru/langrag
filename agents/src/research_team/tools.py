from __future__ import annotations

import json
from typing import Any

import httpx
from langchain_core.tools import tool
from qdrant_client import QdrantClient

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
    )


@tool
def rag_search(query: str, collection: str = "") -> str:
    """Search uploaded documents in the vector store for relevant passages."""
    collection = collection or settings.qdrant_collection
    try:
        client = QdrantClient(url=settings.qdrant_url)
        vectors = get_embeddings([query])
        results = client.search(
            collection_name=collection,
            query_vector=vectors[0],
            limit=settings.rag_top_k,
        )
        if not results:
            return "No relevant documents found in the vector store."

        chunks = []
        for hit in results:
            payload = hit.payload or {}
            chunks.append(
                {
                    "score": hit.score,
                    "text": payload.get("text", ""),
                    "source": payload.get("source", "unknown"),
                    "page": payload.get("page"),
                }
            )
        return json.dumps(chunks, indent=2)
    except Exception as exc:
        return f"RAG search unavailable: {exc}"


@tool
def web_search(query: str) -> str:
    """Search the web for current information on a topic."""
    if not settings.tavily_api_key:
        return "Web search unavailable: TAVILY_API_KEY not configured."

    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query=query, max_results=5)
    return json.dumps(response.get("results", []), indent=2)


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
