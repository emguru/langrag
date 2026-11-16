from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from prometheus_client import Counter, generate_latest
from pydantic import BaseModel, Field, HttpUrl
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
from ingestion.huggingface_ingest import (
    HF_PRESETS,
    ingest_huggingface,
    inspect_dataset,
    list_splits,
)
from ingestion.rag_store import (
    collection_stats,
    delete_document,
    list_chunks,
    list_documents,
)
from ingestion.modality import score_floors
from ingestion.eval_job import run_eval
from ingestion.search import search_baseline

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
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    hnsw_ef: int | None = Field(default=None, ge=16, le=512)
    exact_search: bool = False
    candidate_multiplier: int = Field(default=3, ge=1, le=5)
    hybrid: bool | None = None
    rerank: bool | None = None


class RagSearchResponse(BaseModel):
    query: str
    hits: list[dict]
    collection: str
    top_k: int
    topic: str | None = None
    min_score: float | None = None
    hnsw_ef: int | None = None
    exact_search: bool = False
    candidates_fetched: int | None = None
    candidates_after_floor: int | None = None
    hybrid: bool = True
    reranked: bool = False
    phrase_hits: int | None = None
    score_floors: dict[str, float] | None = None
    score_floor_mode: str | None = None


class HuggingFaceIngestRequest(BaseModel):
    dataset: str = Field(..., examples=["rag-datasets/rag-mini-wikipedia"])
    topic: str = "hf-corpus"
    collection: str = ""
    config: str = ""
    split: str = ""
    text_fields: list[str] = Field(default_factory=lambda: ["text", "passage", "context"])
    id_field: str | None = None
    max_rows: int = Field(default=40, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HuggingFaceIngestResponse(BaseModel):
    dataset: str
    config: str
    split: str
    topic: str
    collection: str
    rows_fetched: int
    documents_indexed: int
    rows_skipped: int
    chunks_indexed: int
    documents: list[dict]
    text_fields: list[str] | None = None
    requested_config: str | None = None
    requested_split: str | None = None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "score_floor_mode": "per_modality",
        "score_floors": score_floors(),
    }


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


@app.get("/ingest/huggingface/presets")
def huggingface_presets():
    return {"presets": HF_PRESETS}


@app.get("/ingest/huggingface/splits")
def huggingface_splits(
    dataset: str = Query(...),
    config: str | None = Query(default=None),
):
    try:
        return {"splits": list_splits(dataset, config=config)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/ingest/huggingface/inspect")
def huggingface_inspect(
    dataset: str = Query(...),
    config: str | None = Query(default=None),
    split: str | None = Query(default=None),
):
    """Resolve real config/split + suggest text columns (fixes most Custom Not found errors)."""
    try:
        return inspect_dataset(dataset, config=config, split=split)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ingest/huggingface", response_model=HuggingFaceIngestResponse)
def ingest_huggingface_endpoint(request: Request, body: HuggingFaceIngestRequest):
    _check_ingest_rate_limit(request)
    collection = _normalize_collection(body.collection)
    try:
        result = ingest_huggingface(
            dataset=body.dataset,
            topic=body.topic,
            collection=collection,
            config=body.config,
            split=body.split,
            text_fields=body.text_fields,
            id_field=body.id_field,
            max_rows=body.max_rows,
            offset=body.offset,
            auto_resolve=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    INGEST_COUNTER.labels(source_type="huggingface").inc()
    CHUNK_COUNTER.inc(result["chunks_indexed"])
    return HuggingFaceIngestResponse(**result)


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
        result = search_baseline(
            body.query,
            collection=collection,
            top_k=body.top_k,
            topic=body.topic,
            min_score=body.min_score,
            hnsw_ef=body.hnsw_ef,
            exact_search=body.exact_search,
            candidate_multiplier=body.candidate_multiplier,
            hybrid=body.hybrid,
            rerank=body.rerank,
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
        top_k=result["top_k"],
        topic=result.get("topic"),
        min_score=result.get("min_score"),
        hnsw_ef=result.get("hnsw_ef"),
        exact_search=bool(result.get("exact_search")),
        candidates_fetched=result.get("candidates_fetched"),
        candidates_after_floor=result.get("candidates_after_floor"),
        hybrid=bool(result.get("hybrid")),
        reranked=bool(result.get("reranked")),
        phrase_hits=result.get("phrase_hits"),
        score_floors=result.get("score_floors"),
        score_floor_mode=result.get("score_floor_mode"),
    )


@app.post("/rag/eval")
def rag_eval(request: Request):
    _check_search_rate_limit(request)
    try:
        return run_eval()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
