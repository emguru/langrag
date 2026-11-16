"""Cheap query assessment — no Bedrock call. Flags ambiguity before retrieval/LLM spend."""

from __future__ import annotations

import re

# Acronyms / domains in this demo corpus where an article ("a"/"an") often shifts sense.
_ACRONYM_SENSES: dict[str, list[str]] = {
    "rag": [
        "Retrieval-Augmented Generation (AI / LLM pattern) — matches corpus topic rag-foundations",
        "A physical rag / cloth (everyday object) — usually not in this corpus",
    ],
    "msa": [
        "Master Services Agreement (legal) — matches eval-legal",
        "Metropolitan Statistical Area / other expansions",
    ],
    "vpc": [
        "Virtual Private Cloud (AWS networking) — matches eval-terraform",
        "Other expansions outside this corpus",
    ],
    "iam": [
        "Identity and Access Management (AWS) — matches eval-terraform / security",
        "Other expansions",
    ],
    "dpa": [
        "Data Processing Addendum (legal) — matches eval-legal",
        "Other expansions",
    ],
}

_ARTICLE_ACRONYM = re.compile(
    r"\b(what\s+is|what's|whats|define|explain)\s+(a|an)\s+([a-z]{2,8})\b",
    re.I,
)
_BARE_ACRONYM = re.compile(
    r"\b(what\s+is|what's|whats|define|explain)\s+([a-z]{2,8})\??\s*$",
    re.I,
)


def assess_query(question: str) -> dict:
    q = (question or "").strip()
    reasons: list[str] = []
    suggestions: list[dict] = []
    senses: list[str] = []
    ambiguous = False

    m_article = _ARTICLE_ACRONYM.search(q)
    m_bare = _BARE_ACRONYM.search(q) if not m_article else None

    token = None
    if m_article:
        token = m_article.group(3).lower()
        ambiguous = True
        reasons.append(
            f'Using "{m_article.group(2)} {token}" often reads as a countable object '
            f'(e.g. "a rag" = cloth), not the acronym {token.upper()}. '
            f'That shifts retrieval away from technical corpus docs.'
        )
    elif m_bare:
        token = m_bare.group(2).lower()
        if token in _ACRONYM_SENSES:
            senses = _ACRONYM_SENSES[token]
            if len(senses) > 1:
                ambiguous = True
                reasons.append(
                    f'"{token.upper()}" has multiple common meanings. '
                    "Without expansion, vector search may still hit the right docs, "
                    "but grounding quality drops if the wrong sense wins."
                )

    if token and token in _ACRONYM_SENSES:
        senses = _ACRONYM_SENSES[token]
        if token == "rag":
            suggestions.extend(
                [
                    {
                        "question": "What is retrieval augmented generation (RAG)?",
                        "why": "Expands the acronym so the search is clearer",
                        "cost": "low",
                    },
                    {
                        "question": "What is RAG in the context of LLMs?",
                        "why": "Keeps the acronym and adds a bit more context",
                        "cost": "low",
                    },
                ]
            )
        else:
            suggestions.append(
                {
                    "question": f"What is {token.upper()} in AWS / enterprise context?",
                    "why": "Adds context so everyday meanings are less likely to win",
                    "cost": "low",
                }
            )

    words = re.findall(r"[a-z0-9]+", q.lower())
    if len(words) <= 3 and not suggestions:
        ambiguous = True
        reasons.append(
            "Very short questions are under-specified for embedding search — "
            "add the domain or full phrase for cheaper, higher-precision retrieval."
        )
        suggestions.append(
            {
                "question": f"{q.rstrip('?')} in retrieval-augmented generation / LLMs?",
                        "why": "Adds a domain cue so search is more precise",
                "cost": "low",
            }
        )

    if not suggestions and ambiguous:
        suggestions.append(
            {
                "question": q,
                "why": "Review the passages below first",
                "cost": "medium",
            }
        )

    return {
        "ambiguous": ambiguous,
        "reasons": reasons,
        "senses": senses,
        "suggestions": suggestions,
        "cheapest_path": (
            "Review the passages, or rephrase the question, before generating an answer."
        ),
    }


def retrieval_confidence(chunks: list[dict], min_top: float = 0.25) -> dict:
    if not chunks:
        return {
            "level": "empty",
            "top_score": 0.0,
            "message": "No matching passages. Try a clearer question or a different topic.",
        }
    top = float(chunks[0].get("score") or 0)
    lead = chunks[0]
    if lead.get("rerank_score") is not None:
        return {
            "level": "ok",
            "top_score": float(lead.get("rerank_score") or top),
            "message": "Passages were re-ordered for this question.",
        }
    if lead.get("phrase_match") or float(lead.get("lexical_score") or 0) >= 0.5:
        return {
            "level": "ok",
            "top_score": max(top, float(lead.get("lexical_score") or 0)),
            "message": "A close wording match was found in the documents.",
        }
    if top < min_top:
        return {
            "level": "low",
            "top_score": top,
            "message": (
                f"Top vector score {top:.3f} is weak (< {min_top}). "
                "These passages may be off-topic. Review them before generating an answer."
            ),
        }
    return {
        "level": "ok",
        "top_score": top,
        "message": f"Top vector score {top:.3f} — corpus likely relevant.",
    }
