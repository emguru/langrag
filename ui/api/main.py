from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://localhost:8123")
INGESTION_URL = os.getenv("INGESTION_URL", "http://localhost:8001")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "research-showcase")

app = FastAPI(title="Showcase API Gateway", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    topic: str
    collection: str = "research_docs"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/ingest/file")
async def proxy_ingest(
    file: UploadFile = File(...),
    topic: str = Form(...),
    collection: str = Form(default="research_docs"),
):
    async with httpx.AsyncClient(timeout=120.0) as client:
        files = {"file": (file.filename, await file.read(), file.content_type)}
        data = {"topic": topic, "collection": collection}
        resp = await client.post(f"{INGESTION_URL}/ingest/file", files=files, data=data)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


async def stream_research(topic: str, collection: str) -> AsyncIterator[str]:
    payload = {
        "assistant_id": "research_team",
        "input": {"topic": topic, "collection_name": collection},
        "stream_mode": "updates",
    }

    yield _sse("status", "Starting research team...")
    run_id = None

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{LANGGRAPH_URL}/runs/stream",
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                yield _sse("error", body.decode())
                return

            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                run_id = event.get("run_id") or run_id
                for node, update in (event.get("data") or {}).items():
                    yield _sse("node", f"{node} completed")
                    if node == "synthesizer" and isinstance(update, dict):
                        report = update.get("report")
                        if report:
                            yield _sse("report", report)

    if run_id:
        yield _sse(
            "langsmith_url",
            f"https://smith.langchain.com/o/default/projects/p/{LANGSMITH_PROJECT}/r/{run_id}",
        )
    yield _sse("done", "Research complete")


def _sse(event_type: str, message: str) -> str:
    return f"data: {json.dumps({'type': event_type, 'message': message})}\n\n"


@app.post("/api/research/stream")
async def research_stream(body: ResearchRequest):
    return StreamingResponse(
        stream_research(body.topic, body.collection),
        media_type="text/event-stream",
    )


# Serve built frontend in production
static_dir = os.getenv("STATIC_DIR", "")
if static_dir and os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
