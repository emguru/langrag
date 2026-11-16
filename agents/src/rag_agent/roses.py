"""ROSES plan built and executed in the agents Lambda — not on a laptop.

Role, Objective, Scenario, Expected Solution, Steps.
Scenario is the live Qdrant retrieval. Generation must follow Expected Solution.
"""

from __future__ import annotations

from typing import Any

ROLE = (
    "Answer only from the retrieved document passages. Cite sources. "
    "If nothing relevant was found, say so instead of inventing an answer."
)

DEFAULT_STEPS = [
    "Understand the question.",
    "Find matching passages in the library.",
    "Decide whether those passages can answer it.",
    "Review the passages if review is on.",
    "Write an answer only from the approved passages.",
]


def start_roses(question: str, topic: str | None, assessment: dict) -> dict[str, Any]:
    """Role + Objective before any vector or LLM spend."""
    q = (question or "").strip()
    objective = q
    if topic:
        objective = f"{q} (topic scope: {topic})"
    if assessment.get("ambiguous") and assessment.get("suggestions"):
        hint = assessment["suggestions"][0].get("question")
        if hint:
            objective = f"{q} — prefer sense: {hint}"
    return {
        "role": ROLE,
        "objective": objective,
        "scenario": {
            "status": "pending_retrieval",
            "chunk_count": 0,
            "sources": [],
            "summary": "Sources have not been searched yet.",
        },
        "expected_solution": (
            "A short answer that satisfies the Objective using only Scenario chunks, "
            "with inline [1] [2] citations — or an explicit refusal if Scenario is empty."
        ),
        "steps": list(DEFAULT_STEPS),
        "where": "agents-lambda",
    }


def bind_scenario(
    roses: dict[str, Any],
    chunks: list[dict],
    confidence: dict | None = None,
) -> dict[str, Any]:
    """Fill Scenario + Expected Solution from live retrieval (still in Lambda)."""
    plan = dict(roses)
    sources: list[str] = []
    for chunk in chunks:
        src = str(chunk.get("source") or "").strip()
        if src and src not in sources:
            sources.append(src)
    previews = []
    for chunk in chunks[:3]:
        text = str(chunk.get("text") or "").strip().replace("\n", " ")
        if text:
            previews.append(text[:160])
    level = (confidence or {}).get("level") or ("empty" if not chunks else "ok")
    if not chunks:
        summary = "No corpus chunks above the score floor. Scenario is empty."
        expected = (
            "Do not invent facts. Say the documents do not support the question and suggest a rephrase."
        )
    elif level in {"low", "empty"}:
        summary = (
            f"{len(chunks)} weak-scoring chunks from {len(sources)} source(s). "
            "Scenario may be off-topic."
        )
        expected = (
            "Only claim what the weak Scenario actually says. "
            "If it does not answer the Objective, refuse and ask for a clearer query."
        )
    else:
        summary = f"{len(chunks)} chunks from {len(sources)} source(s) form the Scenario."
        expected = (
            "Answer the Objective using only these Scenario sources. "
            "Cite each claim with [1], [2] matching the numbered passages. "
            "Do not add facts that are not in the chunks."
        )
        if previews:
            summary += " " + " | ".join(previews)

    plan["scenario"] = {
        "status": "retrieved",
        "chunk_count": len(chunks),
        "sources": sources[:12],
        "summary": summary,
        "confidence": confidence or {},
    }
    plan["expected_solution"] = expected
    return plan


def roses_system_prompt(roses: dict[str, Any], *, ungrounded: bool = False) -> str:
    scenario = roses.get("scenario") or {}
    sources = ", ".join(scenario.get("sources") or []) or "(none)"
    if ungrounded:
        return (
            f"ROLE: {roses.get('role') or ROLE}\n"
            f"OBJECTIVE: {roses.get('objective')}\n"
            "SCENARIO: none — user chose an ungrounded path.\n"
            "EXPECTED SOLUTION: Say the answer is not grounded in the corpus. "
            "Give a brief general note and a better retrieval query. No fake citations.\n"
        )
    return (
        f"ROLE: {roses.get('role') or ROLE}\n"
        f"OBJECTIVE: {roses.get('objective')}\n"
        f"SCENARIO: {scenario.get('summary') or 'unknown'} Sources: {sources}\n"
        f"EXPECTED SOLUTION: {roses.get('expected_solution')}\n"
        "STEPS: Use only Scenario chunks. Cite [1], [2] matching numbered passages. "
        "If Scenario cannot meet Expected Solution, refuse.\n"
    )
