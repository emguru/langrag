from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.limits import RateLimitExceeded, RateLimiter, UploadTooLargeError, read_upload_limited

LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://localhost:8123")
INGESTION_URL = os.getenv("INGESTION_URL", "http://localhost:8001")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "research-showcase")
ASSISTANT_ID = os.getenv("LANGGRAPH_ASSISTANT_ID", "research_team")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
RATE_LIMIT_INGEST_PER_MIN = int(os.getenv("RATE_LIMIT_INGEST_PER_MIN", "10"))
RATE_LIMIT_RESEARCH_START_PER_MIN = int(
    os.getenv("RATE_LIMIT_RESEARCH_START_PER_MIN", os.getenv("RATE_LIMIT_RESEARCH_PER_MIN", "2"))
)
RATE_LIMIT_RESEARCH_RESUME_PER_MIN = int(
    os.getenv("RATE_LIMIT_RESEARCH_RESUME_PER_MIN", "120")
)
RATE_LIMIT_RAG_SEARCH_PER_MIN = int(os.getenv("RATE_LIMIT_RAG_SEARCH_PER_MIN", "30"))
DEFAULT_COLLECTION = os.getenv("DEFAULT_COLLECTION", "research_docs")
ALLOWED_COLLECTIONS = frozenset(
    c.strip()
    for c in os.getenv("ALLOWED_COLLECTIONS", DEFAULT_COLLECTION).split(",")
    if c.strip()
)

app = FastAPI(title="Showcase API Gateway", version="0.1.0")
_rate_limiter = RateLimiter()

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    topic: str
    collection: str = DEFAULT_COLLECTION


class SubQuestionEdit(BaseModel):
    id: str
    question: str
    rationale: str


class ResearchResumeRequest(BaseModel):
    thread_id: str
    checkpoint: str
    sub_questions: list[SubQuestionEdit] | None = None
    synthesize_now: bool | None = None
    edited_report: str | None = None


class RagSearchRequest(BaseModel):
    query: str
    collection: str = DEFAULT_COLLECTION
    top_k: int = Field(default=8, ge=1, le=50)
    topic: str | None = None
    mode: str = ""
    compare: bool = False
    crag: bool | None = None


def _validate_collection(collection: str) -> str:
    if collection not in ALLOWED_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Collection not allowed")
    return collection


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _sse(event_type: str, message: str = "", **extra: object) -> str:
    payload = {"type": event_type, "message": message, **extra}
    return f"data: {json.dumps(payload)}\n\n"


def _sse_json(event_type: str, payload: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


@app.get("/api/health")
def health():
    return {"status": "ok"}


async def _ingestion_request(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.request(method, f"{INGESTION_URL}{path}", **kwargs)
    return resp


@app.post("/api/ingest/preview")
async def ingest_preview(
    request: Request,
    file: UploadFile = File(...),
    topic: str = Form(...),
):
    try:
        _rate_limiter.check(
            f"ingest:{_client_key(request)}",
            limit=RATE_LIMIT_INGEST_PER_MIN,
            window_seconds=60,
        )
        data = await read_upload_limited(file, MAX_UPLOAD_BYTES)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=120.0) as client:
        files = {"file": (file.filename, data, file.content_type)}
        form = {"topic": topic}
        resp = await client.post(f"{INGESTION_URL}/ingest/preview", files=files, data=form)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.post("/api/ingest/file")
async def proxy_ingest(
    request: Request,
    file: UploadFile = File(...),
    topic: str = Form(...),
    collection: str = Form(default=DEFAULT_COLLECTION),
):
    collection = _validate_collection(collection)
    try:
        _rate_limiter.check(
            f"ingest:{_client_key(request)}",
            limit=RATE_LIMIT_INGEST_PER_MIN,
            window_seconds=60,
        )
        data = await read_upload_limited(file, MAX_UPLOAD_BYTES)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=120.0) as client:
        files = {"file": (file.filename, data, file.content_type)}
        form = {"topic": topic, "collection": collection}
        resp = await client.post(f"{INGESTION_URL}/ingest/file", files=files, data=form)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


@app.get("/api/rag/stats")
async def rag_stats(collection: str = Query(default=DEFAULT_COLLECTION)):
    collection = _validate_collection(collection)
    resp = await _ingestion_request("GET", "/rag/stats", params={"collection": collection})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.get("/api/rag/documents")
async def rag_documents(
    collection: str = Query(default=DEFAULT_COLLECTION),
    topic: str | None = Query(default=None),
):
    collection = _validate_collection(collection)
    params: dict[str, str] = {"collection": collection}
    if topic:
        params["topic"] = topic
    resp = await _ingestion_request("GET", "/rag/documents", params=params)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.get("/api/rag/chunks")
async def rag_chunks(
    collection: str = Query(default=DEFAULT_COLLECTION),
    source: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    collection = _validate_collection(collection)
    params: dict[str, str | int] = {"collection": collection, "limit": limit}
    if source:
        params["source"] = source
    if topic:
        params["topic"] = topic
    resp = await _ingestion_request("GET", "/rag/chunks", params=params)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.post("/api/rag/search")
async def rag_search(request: Request, body: RagSearchRequest):
    body.collection = _validate_collection(body.collection)
    try:
        _rate_limiter.check(
            f"rag-search:{_client_key(request)}",
            limit=RATE_LIMIT_RAG_SEARCH_PER_MIN,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    resp = await _ingestion_request(
        "POST",
        "/rag/search",
        json=body.model_dump(),
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.delete("/api/rag/documents")
async def rag_delete_document(
    source: str = Query(...),
    collection: str = Query(default=DEFAULT_COLLECTION),
):
    collection = _validate_collection(collection)
    resp = await _ingestion_request(
        "DELETE",
        "/rag/documents",
        params={"source": source, "collection": collection},
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _parse_sse_data_payload(raw: str) -> object | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def _iter_langgraph_sse_events(
    resp: httpx.Response,
) -> AsyncIterator[tuple[str, object | None]]:
    current_event = ""
    data_parts: list[str] = []

    async for line in resp.aiter_lines():
        if not line:
            if current_event or data_parts:
                yield current_event, _parse_sse_data_payload("".join(data_parts))
                current_event = ""
                data_parts = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
        else:
            try:
                legacy = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(legacy, dict):
                yield legacy.get("event", ""), legacy.get("data", legacy)


_NODE_NEXT_STATUS: dict[str, str] = {
    "coordinator": "Review the research plan before researchers run.",
    "researchers": "Critic reviewing findings...",
    "critic": "Review findings and choose next step.",
    "synthesizer": "Review and edit the final report.",
}


def _format_langgraph_error(data: object) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        message = data.get("message")
        if error == "ReadTimeoutError":
            return (
                "Bedrock request timed out during research. "
                "Retry the run; if it persists, use a shorter topic or switch to Haiku."
            )
        if message and message != "An internal error occurred":
            return f"{error}: {message}" if error else str(message)
        if error:
            return str(error)
    return str(data)


def _report_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        joined = "\n".join(parts).strip()
        return joined or None
    return None


def _node_updates_from_event(event_type: str, data: object) -> list[tuple[str, object]]:
    if not isinstance(data, dict):
        return []

    if event_type in ("updates", "values"):
        return [
            (node, update)
            for node, update in data.items()
            if isinstance(node, str) and not node.startswith("__")
        ]

    if event_type == "error":
        return []

    if "node" in data and "update" in data:
        node = data.get("node")
        if isinstance(node, str) and not node.startswith("__"):
            return [(node, data.get("update"))]

    return [
        (node, update)
        for node, update in data.items()
        if isinstance(node, str) and not node.startswith("__")
    ]


def _serialize_state_values(values: dict[str, Any]) -> dict[str, Any]:
    def _ser(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if isinstance(obj, list):
            return [_ser(item) for item in obj]
        if isinstance(obj, dict):
            return {k: _ser(v) for k, v in obj.items()}
        return obj

    return _ser(values)


def _as_text(value: object, max_len: int | None = None) -> str:
    text = ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block_text = block.get("text")
                if isinstance(block_text, str):
                    parts.append(block_text)
        text = "\n".join(parts)
    elif value is not None:
        text = str(value)
    if max_len is not None and len(text) > max_len:
        return text[:max_len] + "…"
    return text


def _trim_state_for_hitl(values: dict[str, Any]) -> dict[str, Any]:
    """Normalize and cap HITL payload size for reliable SSE + UI rendering."""
    state = _serialize_state_values(values)
    trimmed: dict[str, Any] = {}

    sub_questions: list[dict[str, str]] = []
    for sq in state.get("sub_questions") or []:
        if not isinstance(sq, dict):
            continue
        sub_questions.append(
            {
                "id": str(sq.get("id") or ""),
                "question": _as_text(sq.get("question")),
                "rationale": _as_text(sq.get("rationale")),
            }
        )
    trimmed["sub_questions"] = sub_questions

    findings: list[dict[str, Any]] = []
    for finding in state.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        rag_hits: list[dict[str, Any]] = []
        for hit in finding.get("rag_hits") or []:
            if not isinstance(hit, dict):
                continue
            rag_hits.append(
                {
                    "score": hit.get("score"),
                    "text": _as_text(hit.get("text"), max_len=2000),
                    "source": hit.get("source"),
                    "page": hit.get("page"),
                }
            )
        findings.append(
            {
                "sub_question_id": str(finding.get("sub_question_id") or ""),
                "summary": _as_text(finding.get("summary"), max_len=12000),
                "confidence": str(finding.get("confidence") or "medium"),
                "rag_hits": rag_hits,
            }
        )
    trimmed["findings"] = findings

    critique = state.get("critique")
    if isinstance(critique, dict):
        trimmed["critique"] = {
            "gaps": [_as_text(item) for item in (critique.get("gaps") or [])],
            "contradictions": [
                _as_text(item) for item in (critique.get("contradictions") or [])
            ],
            "follow_up_questions": [
                _as_text(item) for item in (critique.get("follow_up_questions") or [])
            ],
            "ready_to_synthesize": bool(critique.get("ready_to_synthesize")),
        }

    report = _report_text(state.get("report"))
    if report:
        trimmed["report"] = report

    iteration = state.get("iteration")
    if isinstance(iteration, int):
        trimmed["iteration"] = iteration

    return trimmed


def _checkpoint_from_state(
    values: dict[str, Any], state_meta: dict[str, Any] | None = None
) -> str | None:
    if values.get("human_edited_report"):
        return None

    next_nodes = (state_meta or {}).get("next") or []

    # Paused before researchers (initial plan or another research iteration).
    if "researchers" in next_nodes:
        return "plan"

    if values.get("report") and not values.get("human_edited_report"):
        return "report"

    if values.get("critique") is not None and values.get("findings"):
        return "critique"

    if values.get("sub_questions") and not values.get("findings"):
        return "plan"

    return None


async def _fetch_thread_state(
    client: httpx.AsyncClient, thread_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    resp = await client.get(f"{LANGGRAPH_URL}/threads/{thread_id}/state")
    if resp.status_code >= 400:
        return {}, {}
    data = resp.json()
    return data.get("values") or {}, data


async def _create_thread(client: httpx.AsyncClient) -> str:
    resp = await client.post(f"{LANGGRAPH_URL}/threads", json={})
    if resp.status_code >= 400:
        raise RuntimeError(resp.text)
    return resp.json()["thread_id"]


async def _stream_run(
    client: httpx.AsyncClient,
    thread_id: str,
    payload: dict[str, Any],
) -> AsyncIterator[str]:
    run_id = None
    report_sent = False

    async with client.stream(
        "POST",
        f"{LANGGRAPH_URL}/threads/{thread_id}/runs/stream",
        json=payload,
    ) as resp:
        if resp.status_code >= 400:
            body = await resp.aread()
            yield _sse("error", body.decode())
            return

        async for event_type, data in _iter_langgraph_sse_events(resp):
            if event_type == "end":
                break

            if event_type == "metadata" and isinstance(data, dict):
                run_id = data.get("run_id") or run_id
                continue

            if event_type == "error" or (isinstance(data, dict) and data.get("error")):
                yield _sse("error", _format_langgraph_error(data))
                return

            if isinstance(data, dict) and "__interrupt__" in data:
                values, state_meta = await _fetch_thread_state(client, thread_id)
                checkpoint = _checkpoint_from_state(values, state_meta)
                if checkpoint:
                    yield _sse_json(
                        "interrupt",
                        {
                            "thread_id": thread_id,
                            "checkpoint": checkpoint,
                        },
                    )
                return

            for node, update in _node_updates_from_event(event_type, data):
                yield _sse("node", f"{node} completed")
                next_status = _NODE_NEXT_STATUS.get(node)
                if next_status:
                    yield _sse("status", next_status)

                if node == "researchers" and isinstance(update, dict):
                    findings = update.get("findings") or []
                    rag_hits = []
                    for finding in findings:
                        if isinstance(finding, dict):
                            rag_hits.extend(finding.get("rag_hits") or [])
                    if rag_hits:
                        yield _sse_json("rag_hits", {"hits": rag_hits[:20]})

                if node == "synthesizer" and isinstance(update, dict):
                    report = _report_text(update.get("report"))
                    if report:
                        report_sent = True
                        yield _sse("report", report)

    values, state_meta = await _fetch_thread_state(client, thread_id)
    checkpoint = _checkpoint_from_state(values, state_meta)
    if checkpoint and not report_sent:
        yield _sse_json(
            "interrupt",
            {
                "thread_id": thread_id,
                "checkpoint": checkpoint,
            },
        )
        return

    if not report_sent:
        report = _report_text(values.get("human_edited_report")) or _report_text(
            values.get("report")
        )
        if report:
            report_sent = True
            yield _sse("report", report)

    if not report_sent:
        yield _sse(
            "error",
            "Research run finished without a report. Check agent logs.",
        )
        return

    if run_id:
        yield _sse(
            "langsmith_url",
            f"https://smith.langchain.com/o/default/projects/p/{LANGSMITH_PROJECT}/r/{run_id}",
        )
    yield _sse("done", "Research complete")


async def stream_research_start(topic: str, collection: str) -> AsyncIterator[str]:
    yield _sse("status", "Starting research team...")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        ) as client:
            try:
                health = await client.get(f"{LANGGRAPH_URL}/ok")
                if health.status_code >= 400:
                    yield _sse("error", f"Research agents unhealthy (HTTP {health.status_code})")
                    return
            except httpx.RequestError as exc:
                yield _sse("error", f"Cannot reach research agents: {exc}")
                return

            thread_id = await _create_thread(client)
            yield _sse_json("thread", {"thread_id": thread_id})

            payload = {
                "assistant_id": ASSISTANT_ID,
                "input": {"topic": topic, "collection_name": collection},
                "stream_mode": "updates",
            }
            async for event in _stream_run(client, thread_id, payload):
                yield event
    except httpx.RequestError as exc:
        yield _sse("error", f"Research stream failed: {exc}")


async def stream_research_resume(body: ResearchResumeRequest) -> AsyncIterator[str]:
    update: dict[str, Any] = {}
    if body.checkpoint == "plan" and body.sub_questions is not None:
        update["sub_questions"] = [sq.model_dump() for sq in body.sub_questions]
    elif body.checkpoint == "critique":
        if body.synthesize_now is None:
            yield _sse("error", "synthesize_now is required for critique checkpoint")
            return
        update["human_synthesize_now"] = body.synthesize_now
    elif body.checkpoint == "report":
        update["human_edited_report"] = body.edited_report or ""
    else:
        yield _sse("error", f"Unknown checkpoint: {body.checkpoint}")
        return

    payload = {
        "assistant_id": ASSISTANT_ID,
        "command": {"update": update, "resume": True},
        "stream_mode": "updates",
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        ) as client:
            async for event in _stream_run(client, body.thread_id, payload):
                yield event
    except httpx.RequestError as exc:
        yield _sse("error", f"Research resume failed: {exc}")


@app.get("/api/research/state")
async def research_state(thread_id: str):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            values, state_meta = await _fetch_thread_state(client, thread_id)
            if not values:
                raise HTTPException(status_code=404, detail="Thread not found")
            checkpoint = _checkpoint_from_state(values, state_meta)
            return {
                "thread_id": thread_id,
                "checkpoint": checkpoint,
                "state": _trim_state_for_hitl(values),
            }
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502, detail=f"Cannot reach research agents: {exc}"
        ) from exc


@app.post("/api/research/stream")
async def research_stream(request: Request, body: ResearchRequest):
    body.collection = _validate_collection(body.collection)
    try:
        _rate_limiter.check(
            f"research-start:{_client_key(request)}",
            limit=RATE_LIMIT_RESEARCH_START_PER_MIN,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return StreamingResponse(
        stream_research_start(body.topic, body.collection),
        media_type="text/event-stream",
    )


@app.post("/api/research/resume")
async def research_resume(request: Request, body: ResearchResumeRequest):
    try:
        _rate_limiter.check(
            f"research-resume:{_client_key(request)}",
            limit=RATE_LIMIT_RESEARCH_RESUME_PER_MIN,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return StreamingResponse(
        stream_research_resume(body),
        media_type="text/event-stream",
    )


static_dir = os.getenv("STATIC_DIR", "")
if static_dir and os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
