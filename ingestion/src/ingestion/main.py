from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from prometheus_client import Counter, generate_latest
from pydantic import BaseModel, HttpUrl
from starlette.responses import PlainTextResponse

from ingestion.config import settings
from ingestion.pipeline import fetch_url, ingest_bytes

INGEST_COUNTER = Counter("ingestion_documents_total", "Documents ingested", ["source_type"])
CHUNK_COUNTER = Counter("ingestion_chunks_total", "Chunks indexed")

app = FastAPI(title="RAG Ingestion Service", version="0.1.0")


class UrlIngestRequest(BaseModel):
    url: HttpUrl
    topic: str
    collection: str = ""


class IngestResponse(BaseModel):
    document_id: str
    source: str
    chunks_indexed: int
    collection: str
    topic: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest())


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    topic: str = Form(...),
    collection: str = Form(default=""),
):
    collection = collection or settings.qdrant_collection
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        result = ingest_bytes(data, file.filename or "upload.bin", topic, collection)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    INGEST_COUNTER.labels(source_type="file").inc()
    CHUNK_COUNTER.inc(result["chunks_indexed"])
    return result


@app.post("/ingest/url", response_model=IngestResponse)
async def ingest_url(body: UrlIngestRequest):
    collection = body.collection or settings.qdrant_collection
    try:
        data, filename = await fetch_url(str(body.url))
        result = ingest_bytes(data, filename, body.topic, collection)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    INGEST_COUNTER.labels(source_type="url").inc()
    CHUNK_COUNTER.inc(result["chunks_indexed"])
    return result
