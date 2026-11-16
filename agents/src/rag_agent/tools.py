from __future__ import annotations

import json

import httpx

from rag_agent.config import settings


def retrieve_chunks(
    question: str,
    topic: str | None = None,
    top_k: int | None = None,
    *,
    min_score: float | None = None,
    hnsw_ef: int | None = None,
    exact_search: bool = False,
) -> list[dict]:
    payload: dict = {
        "query": question,
        "top_k": top_k or settings.rag_top_k,
        "exact_search": exact_search,
        "candidate_multiplier": 3,
        "hybrid": True,
        "rerank": True,
    }
    if topic:
        payload["topic"] = topic
    if min_score is not None:
        payload["min_score"] = min_score
    if hnsw_ef is not None:
        payload["hnsw_ef"] = hnsw_ef
    if settings.ingestion_function_name:
        return _hits_via_lambda(payload)
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(f"{settings.ingestion_api_url}/rag/search", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data.get("hits") or []


def _hits_via_lambda(payload: dict) -> list[dict]:
    import boto3

    client = boto3.client("lambda", region_name=settings.aws_region)
    event = {
        "version": "2.0",
        "routeKey": "POST /rag/search",
        "rawPath": "/rag/search",
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "requestContext": {
            "accountId": "internal",
            "apiId": "internal",
            "domainName": "internal",
            "domainPrefix": "internal",
            "http": {
                "method": "POST",
                "path": "/rag/search",
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "rag-agent",
            },
            "requestId": "agents-retrieve",
            "routeKey": "POST /rag/search",
            "stage": "$default",
            "timeEpoch": 0,
        },
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }
    response = client.invoke(
        FunctionName=settings.ingestion_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event),
    )
    raw = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        raise RuntimeError(raw.get("errorMessage") or raw.get("errorType") or "Search invoke failed")
    if not isinstance(raw, dict) or "statusCode" not in raw:
        raise RuntimeError(f"Unexpected search response: {raw}")
    status = int(raw["statusCode"])
    body = raw.get("body")
    data = json.loads(body) if isinstance(body, str) else (body or {})
    if status >= 400:
        raise RuntimeError(str(data.get("detail") or data.get("error") or data))
    return data.get("hits") or []


def merge_hits(primary: list[dict], extra: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for hit in list(primary) + list(extra):
        hid = str(hit.get("id") or "")
        key = hid or f"{hit.get('source')}:{hit.get('chunk_index')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def rewrite_query(question: str) -> str:
    """Cheap rewrite for a second retrieve when the first pass is weak."""
    q = (question or "").strip()
    if not q:
        return q
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from rag_agent.config import settings as cfg

        if cfg.llm_provider == "openai" and cfg.openai_api_key:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=64)
        else:
            from langchain_aws import ChatBedrockConverse

            llm = ChatBedrockConverse(
                model=cfg.default_llm_model,
                region_name=cfg.bedrock_region,
                temperature=0,
                max_tokens=64,
            )
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Rewrite the user question as a short document-search query. "
                        "Output only the query. Do not answer it."
                    )
                ),
                HumanMessage(content=q),
            ]
        )
        content = response.content
        if isinstance(content, list):
            text = "".join(str(part) for part in content)
        else:
            text = str(content)
        rewritten = " ".join(text.strip().split())
        return rewritten or q
    except Exception:
        return q


def format_context(chunks: list[dict]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "")
        score = chunk.get("score")
        header = f"[{i}] source={source}"
        if score is not None:
            header += f" score={score:.3f}"
        parts.append(f"{header}\n{text}")
    return "\n\n".join(parts)


def generate_answer(
    question: str,
    chunks: list[dict],
    *,
    ungrounded: bool = False,
    roses: dict | None = None,
) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from rag_agent.config import settings as cfg
    from rag_agent.roses import roses_system_prompt
    from rag_agent.state import SYSTEM_PROMPT, UNGROUNDED_PROMPT

    if roses:
        system = roses_system_prompt(roses, ungrounded=ungrounded or not chunks)
    elif ungrounded or not chunks:
        system = UNGROUNDED_PROMPT
    else:
        system = SYSTEM_PROMPT

    if ungrounded or not chunks:
        user_content = (
            f"Question: {question}\n\n"
            "No corpus context was provided. Answer carefully and recommend a better retrieval query."
        )
    else:
        context = format_context(chunks)
        user_content = f"Question: {question}\n\nContext:\n{context}"

    if cfg.llm_provider == "openai" and cfg.openai_api_key:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
    else:
        from langchain_aws import ChatBedrockConverse

        llm = ChatBedrockConverse(
            model=cfg.default_llm_model,
            region_name=cfg.bedrock_region,
            temperature=0.2,
            max_tokens=cfg.rag_max_tokens,
        )

    response = llm.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=user_content),
        ]
    )
    content = response.content
    if isinstance(content, list):
        return "".join(str(part) for part in content)
    return str(content)


def chunks_for_resume(raw: object) -> list[dict]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "chunks" in raw:
        return list(raw["chunks"])
    return []
