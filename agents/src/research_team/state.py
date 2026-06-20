from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field

from research_team.config import settings


class ResearchSubQuestion(BaseModel):
    id: str
    question: str
    rationale: str


class CoordinatorOutput(BaseModel):
    topic: str
    sub_questions: list[ResearchSubQuestion]
    strategy: str


class ResearchFinding(BaseModel):
    sub_question_id: str
    summary: str
    sources: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    rag_hits: list[dict] = Field(default_factory=list)


class CritiqueOutput(BaseModel):
    gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    ready_to_synthesize: bool = False


class ResearchState(TypedDict, total=False):
    topic: str
    messages: Annotated[list[AnyMessage], operator.add]
    sub_questions: list[ResearchSubQuestion]
    findings: Annotated[list[ResearchFinding], operator.add]
    critique: CritiqueOutput | None
    report: str
    iteration: int
    collection_name: str
    human_synthesize_now: bool
    human_edited_report: str


def initial_state(topic: str, collection: str | None = None) -> ResearchState:
    return {
        "topic": topic,
        "messages": [],
        "sub_questions": [],
        "findings": [],
        "critique": None,
        "report": "",
        "iteration": 0,
        "collection_name": collection or settings.qdrant_collection,
        "human_synthesize_now": False,
        "human_edited_report": "",
    }


COORDINATOR_PROMPT = """You are a research coordinator. Given a topic, decompose it into
{max_parallel} focused sub-questions that can be researched in parallel.

Return structured output with:
- topic (restated clearly)
- sub_questions (each with id like "sq-1", question, rationale)
- strategy (brief plan for how researchers should approach this)

Prioritize questions that benefit from both document retrieval (RAG) and web search."""

RESEARCHER_PROMPT = """You are a specialist researcher. Investigate ONE sub-question thoroughly.

Use rag_search for uploaded documents (Corrective RAG: grades chunks, filters noise, may fall back to web).
Use web_search for current events when documents are insufficient.
Cite sources. Be concise but substantive. Rate your confidence: high, medium, or low."""

CRITIC_PROMPT = """You are a research critic. Review all findings for:
- Gaps (missing perspectives or data)
- Contradictions between sources
- Whether more research is needed

Set ready_to_synthesize=true only when findings are sufficient for a quality report."""

SYNTHESIZER_PROMPT = """You are a research synthesizer. Write a polished report with:
1. Executive summary
2. Key findings (with citations)
3. Open questions / limitations
4. Recommended next steps

Use markdown formatting. Ground every claim in the provided findings."""


def coerce_sub_question(item: ResearchSubQuestion | dict) -> ResearchSubQuestion:
    if isinstance(item, ResearchSubQuestion):
        return item
    return ResearchSubQuestion.model_validate(item)


def coerce_finding(item: ResearchFinding | dict) -> ResearchFinding:
    if isinstance(item, ResearchFinding):
        return item
    return ResearchFinding.model_validate(item)


def coerce_critique(item: CritiqueOutput | dict | None) -> CritiqueOutput | None:
    if item is None:
        return None
    if isinstance(item, CritiqueOutput):
        return item
    return CritiqueOutput.model_validate(item)
