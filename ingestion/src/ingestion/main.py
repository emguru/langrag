from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from prometheus_client import Counter, generate_latest
from pydantic import BaseModel, Field, HttpUrl, model_validator
from starlette.responses import PlainTextResponse

from ingestion.config import settings
from ingestion.limits import (
    FetchTooLargeError,
    RateLimitExceeded,
    RateLimiter,
    UploadTooLargeError,
    read_upload_limited,
)
from ingestion.pipeline import fetch_url, ingest_bytes
from ingestion.preview import preview_bytes
from ingestion.rag_store import (
    collection_stats,
    delete_document,
    list_chunks,
    list_documents,
)
from ingestion.augmentation import search_with_augmentation

INGEST_COUNTER = Counter("ingestion_documents_total", "Documents ingested", ["source_type"])
CHUNK_COUNTER = Counter("ingestion_chunks_total", "Chunks indexed")
SEARCH_COUNTER = Counter("rag_search_queries_total", "Direct RAG search queries")

app = FastAPI(title="RAG Ingestion Service", version="0.1.0")
_rate_limiter = RateLimiter()


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


class RagSearchRequest(BaseModel):
    query: str
    collection: str = ""
    top_k: int = Field(default=8, ge=1, le=50)
    topic: str | None = None
    mode: str = Field(
        default="",
        description="baseline | swag | crag | swag_crag (legacy crag bool used if empty)",
    )
    compare: bool = False
    crag: bool | None = None

    @model_validator(mode="after")
    def resolve_mode(self) -> RagSearchRequest:
        if self.mode:
            allowed = {"baseline", "swag", "crag", "swag_crag"}
            if self.mode not in allowed:
                raise ValueError(f"mode must be one of {sorted(allowed)}")
            return self
        if self.crag is False:
            self.mode = "baseline"
        elif self.crag is True or self.crag is None:
            self.mode = "crag" if settings.crag_enabled else "baseline"
        return self


class RagSearchResponse(BaseModel):
    query: str
    hits: list[dict]
    collection: str
    mode: str = "baseline"
    swag: dict | None = None
    crag: dict | None = None
    baseline_hits: list[dict] | None = None
    comparison: dict | None = None
    lift: dict | None = None
    confidence: dict | None = None
    expansion: dict | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest())


def _normalize_collection(collection: str) -> str:
    name = collection or settings.qdrant_collection
    if name != settings.qdrant_collection:
        raise HTTPException(status_code=400, detail="Collection not allowed")
    return name


def _check_ingest_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    try:
        _rate_limiter.check(
            f"ingest:{client}",
            limit=settings.rate_limit_ingest_per_min,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def _check_search_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    try:
        _rate_limiter.check(
            f"search:{client}",
            limit=settings.rate_limit_search_per_min,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.post("/ingest/preview")
async def ingest_preview(
    request: Request,
    file: UploadFile = File(...),
    topic: str = Form(...),
):
    _check_ingest_rate_limit(request)
    try:
        data = await read_upload_limited(file, settings.max_upload_bytes)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return preview_bytes(data, file.filename or "upload.bin", topic)


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    topic: str = Form(...),
    collection: str = Form(default=""),
):
    _check_ingest_rate_limit(request)
    collection = _normalize_collection(collection)
    try:
        data = await read_upload_limited(file, settings.max_upload_bytes)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
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
async def ingest_url(request: Request, body: UrlIngestRequest):
    _check_ingest_rate_limit(request)
    collection = _normalize_collection(body.collection)
    try:
        data, filename = await fetch_url(str(body.url))
        result = ingest_bytes(data, filename, body.topic, collection)
    except FetchTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    INGEST_COUNTER.labels(source_type="url").inc()
    CHUNK_COUNTER.inc(result["chunks_indexed"])
    return result


@app.get("/rag/stats")
def rag_stats(collection: str = Query(default="")):
    try:
        return collection_stats(collection)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/rag/documents")
def rag_documents(
    collection: str = Query(default=""),
    topic: str | None = Query(default=None),
):
    try:
        return {"documents": list_documents(collection, topic=topic)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/rag/chunks")
def rag_chunks(
    collection: str = Query(default=""),
    source: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        return {
            "chunks": list_chunks(
                collection, source=source, topic=topic, limit=limit
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/rag/search", response_model=RagSearchResponse)
def rag_search(request: Request, body: RagSearchRequest):
    _check_search_rate_limit(request)
    collection = _normalize_collection(body.collection)
    try:
        result = search_with_augmentation(
            body.query,
            collection=collection,
            top_k=body.top_k,
            topic=body.topic,
            mode=body.mode,  # type: ignore[arg-type]
            compare=body.compare,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    SEARCH_COUNTER.inc()
    return RagSearchResponse(
        query=result["query"],
        hits=result["hits"],
        collection=result["collection"],
        mode=result.get("mode", body.mode),
        swag=result.get("swag"),
        crag=result.get("crag"),
        baseline_hits=result.get("baseline_hits"),
        comparison=result.get("comparison"),
        lift=result.get("lift"),
        confidence=result.get("confidence"),
        expansion=result.get("expansion"),
    )


@app.delete("/rag/documents")
def rag_delete_document(
    source: str = Query(...),
    collection: str = Query(default=""),
):
    try:
        deleted = delete_document(source, collection)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted_points": deleted, "source": source}
