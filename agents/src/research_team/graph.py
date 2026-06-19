from __future__ import annotations

import asyncio
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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
)
from research_team.tools import RESEARCH_TOOLS, get_llm


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
    }


async def _research_one(sub_q, collection: str) -> ResearchFinding:
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
    return ResearchFinding(
        sub_question_id=sub_q.id,
        summary=content,
        sources=[],
        confidence="medium",
    )


def researchers_node(state: ResearchState) -> dict:
    sub_qs = state.get("sub_questions", [])[: settings.max_researcher_parallel]

    async def run_all():
        tasks = [_research_one(sq, state.get("collection_name", settings.qdrant_collection)) for sq in sub_qs]
        return await asyncio.gather(*tasks)

    findings = asyncio.run(run_all())
    return {"findings": findings}


def critic_node(state: ResearchState) -> dict:
    llm = get_llm().with_structured_output(CritiqueOutput)
    findings = state.get("findings", [])
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
    llm = get_llm()
    findings = state.get("findings", [])
    findings_text = "\n\n".join(
        f"### {f.sub_question_id}\n{f.summary}" for f in findings
    )
    critique = state.get("critique")
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
    return {"report": response.content, "messages": [response]}


def route_after_critic(state: ResearchState) -> Literal["synthesizer", "coordinator"]:
    critique = state.get("critique")
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

    return builder.compile()


graph = build_graph()
