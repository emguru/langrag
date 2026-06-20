from __future__ import annotations

import asyncio
import json
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from research_team.config import settings
from research_team.state import (
    COORDINATOR_PROMPT,
    CRITIC_PROMPT,
    CritiqueOutput,
    CoordinatorOutput,
    ResearchFinding,
    ResearchState,
    RESEARCHER_PROMPT,
    SYNTHESIZER_PROMPT,
    coerce_critique,
    coerce_finding,
    coerce_sub_question,
)
from research_team.tools import RESEARCH_TOOLS, get_llm


def _message_content_to_str(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _extract_rag_hits(messages: list) -> list[dict]:
    hits: list[dict] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if msg.name and msg.name != "rag_search":
            continue
        try:
            parsed = json.loads(str(msg.content))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, list):
            hits.extend(parsed)
        elif isinstance(parsed, dict):
            chunk_hits = parsed.get("hits")
            if isinstance(chunk_hits, list):
                hits.extend(chunk_hits)
    return hits


def coordinator_node(state: ResearchState) -> dict:
    llm = get_llm().with_structured_output(CoordinatorOutput)
    prompt = COORDINATOR_PROMPT.format(max_parallel=settings.max_researcher_parallel)
    result: CoordinatorOutput = llm.invoke(
        [
            SystemMessage(content=prompt),
            HumanMessage(content=f"Research topic: {state['topic']}"),
        ]
    )
    return {
        "sub_questions": result.sub_questions,
        "messages": [AIMessage(content=f"Plan: {result.strategy}")],
        "human_synthesize_now": False,
    }


async def _research_one(sub_q_raw, collection: str) -> ResearchFinding:
    sub_q = coerce_sub_question(sub_q_raw)
    llm = get_llm().bind_tools(RESEARCH_TOOLS)
    tool_node = ToolNode(RESEARCH_TOOLS)

    messages = [
        SystemMessage(content=RESEARCHER_PROMPT),
        HumanMessage(
            content=(
                f"Sub-question ({sub_q.id}): {sub_q.question}\n"
                f"Context: {sub_q.rationale}\n"
                f"Use collection: {collection}"
            )
        ),
    ]

    for _ in range(3):
        response = await llm.ainvoke(messages)
        messages.append(response)
        if not response.tool_calls:
            break
        tool_results = await tool_node.ainvoke({"messages": messages})
        messages.extend(tool_results["messages"])

    final = messages[-1]
    content = final.content if hasattr(final, "content") else str(final)
    rag_hits = _extract_rag_hits(messages)
    sources = sorted(
        {
            str(hit.get("source"))
            for hit in rag_hits
            if isinstance(hit, dict) and hit.get("source")
        }
    )
    return ResearchFinding(
        sub_question_id=sub_q.id,
        summary=_message_content_to_str(content),
        sources=sources,
        confidence="medium",
        rag_hits=rag_hits,
    )


def researchers_node(state: ResearchState) -> dict:
    raw = state.get("sub_questions", [])[: settings.max_researcher_parallel]
    sub_qs = [coerce_sub_question(sq) for sq in raw]

    async def run_all():
        tasks = [
            _research_one(sq, state.get("collection_name", settings.qdrant_collection))
            for sq in sub_qs
        ]
        return await asyncio.gather(*tasks)

    findings = asyncio.run(run_all())
    return {"findings": findings}


def critic_node(state: ResearchState) -> dict:
    llm = get_llm().with_structured_output(CritiqueOutput)
    findings = [coerce_finding(f) for f in state.get("findings", [])]
    findings_text = "\n\n".join(
        f"[{f.sub_question_id}] ({f.confidence}) {f.summary}" for f in findings
    )
    critique: CritiqueOutput = llm.invoke(
        [
            SystemMessage(content=CRITIC_PROMPT),
            HumanMessage(
                content=f"Topic: {state['topic']}\n\nFindings:\n{findings_text}"
            ),
        ]
    )
    return {"critique": critique, "iteration": state.get("iteration", 0) + 1}


def synthesizer_node(state: ResearchState) -> dict:
    edited = state.get("human_edited_report", "").strip()
    if edited:
        return {"report": edited, "messages": [AIMessage(content=edited)]}

    llm = get_llm()
    findings = [coerce_finding(f) for f in state.get("findings", [])]
    findings_text = "\n\n".join(
        f"### {f.sub_question_id}\n{f.summary}" for f in findings
    )
    critique = coerce_critique(state.get("critique"))
    gaps = critique.gaps if critique else []
    response = llm.invoke(
        [
            SystemMessage(content=SYNTHESIZER_PROMPT),
            HumanMessage(
                content=(
                    f"Topic: {state['topic']}\n\n"
                    f"Findings:\n{findings_text}\n\n"
                    f"Known gaps: {gaps}"
                )
            ),
        ]
    )
    report = _message_content_to_str(response.content)
    return {
        "report": report,
        "messages": [response],
    }


def route_after_critic(state: ResearchState) -> Literal["synthesizer", "coordinator"]:
    if state.get("human_synthesize_now"):
        return "synthesizer"
    critique = coerce_critique(state.get("critique"))
    if critique and critique.ready_to_synthesize:
        return "synthesizer"
    if state.get("iteration", 0) >= settings.max_iterations:
        return "synthesizer"
    return "coordinator"


def build_graph():
    builder = StateGraph(ResearchState)
    builder.add_node("coordinator", coordinator_node)
    builder.add_node("researchers", researchers_node)
    builder.add_node("critic", critic_node)
    builder.add_node("synthesizer", synthesizer_node)

    builder.add_edge(START, "coordinator")
    builder.add_edge("coordinator", "researchers")
    builder.add_edge("researchers", "critic")
    builder.add_conditional_edges("critic", route_after_critic)
    builder.add_edge("synthesizer", END)

    return builder.compile(
        interrupt_before=["researchers"],
        interrupt_after=["critic", "synthesizer"],
    )


graph = build_graph()
