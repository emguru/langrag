from __future__ import annotations

from typing import TypedDict


class RAGState(TypedDict, total=False):
    question: str
    topic: str | None
    top_k: int
    min_score: float
    hnsw_ef: int
    exact_search: bool
    chunks: list[dict]
    approved_chunks: list[dict]
    answer: str
    hitl_enabled: bool
    allow_ungrounded: bool
    assessment: dict
    confidence: dict
    skip_llm: bool
    roses: dict


SYSTEM_PROMPT = """You answer questions using only the provided context chunks.
Cite supporting passages as [1], [2] matching the numbered context blocks. Do not invent numbers.
If context is insufficient, say so clearly. Be concise and factual."""

UNGROUNDED_PROMPT = """You have NO corpus context. Say clearly that the answer is not grounded in the
user documents. Give a brief general answer and recommend a clearer retrieval query.
Do not invent citations."""
