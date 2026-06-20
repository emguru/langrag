from __future__ import annotations

import io
import uuid
from pathlib import PurePosixPath

import boto3
import httpx
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ingestion.config import settings
from ingestion.limits import read_response_limited
from ingestion.url_safety import UnsafeUrlError, validate_fetch_url


def get_embedder():
    if settings.llm_provider == "openai" and settings.openai_api_key:
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model="text-embedding-3-small")

    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(
        model_id=settings.default_embedding_model,
        region_name=settings.bedrock_region,
    )


def get_s3_client():
    return boto3.client("s3", region_name=settings.aws_region)


def ensure_collection(client: QdrantClient, collection: str, vector_size: int = 1024):
    collections = [c.name for c in client.get_collections().collections]
    if collection not in collections:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def extract_text_from_pdf(data: bytes) -> list[tuple[str, int | None]]:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append((text, i + 1))
    return pages


def extract_text_from_bytes(data: bytes, filename: str) -> list[tuple[str, int | None]]:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(data)
    text = data.decode("utf-8", errors="ignore")
    return [(text, None)] if text.strip() else []


def chunk_documents(pages: list[tuple[str, int | None]], source: str) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = []
    for text, page in pages:
        for i, chunk in enumerate(splitter.split_text(text)):
            chunks.append(
                {
                    "text": chunk,
                    "source": source,
                    "page": page,
                    "chunk_index": i,
                }
            )
    return chunks


def embed_and_upsert(
    chunks: list[dict], collection: str, topic: str, doc_id: str | None = None
) -> int:
    if not chunks:
        return 0

    embedder = get_embedder()
    client = QdrantClient(url=settings.qdrant_url)

    # Probe vector size
    sample = embedder.embed_documents(["dimension probe"])
    ensure_collection(client, collection, vector_size=len(sample[0]))

    points = []
    for batch_start in range(0, len(chunks), settings.batch_size):
        batch = chunks[batch_start : batch_start + settings.batch_size]
        texts = [c["text"] for c in batch]
        vectors = embedder.embed_documents(texts)
        for chunk, vector in zip(batch, vectors):
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        **chunk,
                        "topic": topic,
                        **({"document_id": doc_id} if doc_id else {}),
                    },
                )
            )

    client.upsert(collection_name=collection, points=points)
    return len(points)


def upload_to_s3(data: bytes, key: str) -> str:
    s3 = get_s3_client()
    s3.put_object(Bucket=settings.s3_document_bucket, Key=key, Body=data)
    return f"s3://{settings.s3_document_bucket}/{key}"


async def fetch_url(url: str, max_bytes: int | None = None) -> tuple[bytes, str]:
    limit = max_bytes if max_bytes is not None else settings.max_fetch_bytes
    validate_fetch_url(url)
    current = url
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        for _ in range(5):
            resp = await client.get(current)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    resp.raise_for_status()
                next_url = str(httpx.URL(current).join(location))
                validate_fetch_url(next_url)
                current = next_url
                continue
            resp.raise_for_status()
            filename = current.rstrip("/").split("/")[-1] or "document.html"
            data = await read_response_limited(resp, limit)
            return data, filename
    raise UnsafeUrlError("Too many redirects")


def ingest_bytes(data: bytes, filename: str, topic: str, collection: str) -> dict:
    doc_id = str(uuid.uuid4())
    s3_key = f"{topic}/{doc_id}/{filename}"
    source = upload_to_s3(data, s3_key)

    pages = extract_text_from_bytes(data, filename)
    chunks = chunk_documents(pages, source=source)
    count = embed_and_upsert(chunks, collection, topic, doc_id=doc_id)

    return {
        "document_id": doc_id,
        "source": source,
        "chunks_indexed": count,
        "collection": collection,
        "topic": topic,
    }
