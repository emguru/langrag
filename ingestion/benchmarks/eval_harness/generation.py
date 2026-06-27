"""Grounded answer generation from retrieved hits (extractive default, optional LLM)."""

from __future__ import annotations

import re
from typing import Any

from validation_eval import hit_seed_key

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "") if len(t) >= 3}


def _split_sentences(text: str) -> list[str]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(cleaned) if part.strip()]
    return parts or [cleaned]


def _query_overlap(query: str, sentence: str) -> int:
    q = _tokens(query)
    s = _tokens(sentence)
    return len(q & s)


def _format_context(hits: list[dict], *, limit: int = 3, max_chars: int = 1200) -> str:
    blocks: list[str] = []
    used = 0
    for idx, hit in enumerate(hits[:limit], start=1):
        key = hit_seed_key(hit) or str(hit.get("source") or f"chunk-{idx}")
        text = " ".join(str(hit.get("text") or "").split())
        if not text:
            continue
        room = max_chars - used
        if room <= 0:
            break
        snippet = text[: min(len(text), room)]
        blocks.append(f"[{idx}] source={key}\n{snippet}")
        used += len(snippet)
    return "\n\n".join(blocks)


def extractive_answer(query: str, hits: list[dict]) -> tuple[str, dict[str, Any]]:
    """Compose a short answer from the top hit(s) — no LLM."""
    if not hits:
        return (
            "Insufficient evidence in retrieved documents.",
            {"cited_seed_key": None, "no_hits": True, "mode": "extractive"},
        )

    top = hits[0]
    text = str(top.get("text") or "").strip()
    sentences = _split_sentences(text)
    if sentences:
        best = max(sentences, key=lambda s: (_query_overlap(query, s), len(s)))
        answer = best
        if len(answer) < 50 and len(text) > len(answer):
            answer = text[: min(420, len(text))]
    else:
        answer = text[:420]

    return answer, {
        "cited_seed_key": hit_seed_key(top),
        "cited_source": top.get("source"),
        "no_hits": False,
        "mode": "extractive",
    }


def llm_answer(query: str, hits: list[dict]) -> tuple[str, dict[str, Any]]:
    """Generate a grounded answer with the configured grader LLM."""
    if not hits:
        return extractive_answer(query, hits)

    from ingestion.config import settings
    from ingestion.crag import _get_grader_llm, _truncate

    context = _format_context(hits)
    prompt = (
        "Answer the question using ONLY the retrieved context below.\n"
        "If the context is insufficient, respond exactly: "
        "Insufficient evidence in retrieved documents.\n"
        "Keep the answer to 1-3 sentences. Do not invent facts.\n\n"
        f"Question: {query}\n\nContext:\n{_truncate(context, 3500)}\n\nAnswer:"
    )
    llm = _get_grader_llm()
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    answer = str(content).strip()
    top = hits[0]
    return answer, {
        "cited_seed_key": hit_seed_key(top),
        "cited_source": top.get("source"),
        "no_hits": False,
        "mode": "llm",
    }


def generate_answer(
    query: str,
    hits: list[dict],
    *,
    mode: str = "extractive",
) -> tuple[str, dict[str, Any]]:
    if mode == "llm":
        return llm_answer(query, hits)
    return extractive_answer(query, hits)
