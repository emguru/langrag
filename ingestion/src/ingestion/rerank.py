"""Amazon Rerank 1.0 only. Cohere Rerank 3.5 and OpenSearch are rejected."""

from __future__ import annotations

import json
import logging

from botocore.exceptions import ClientError

from ingestion.config import settings

logger = logging.getLogger(__name__)

AMAZON_RERANK_MODEL_ID = "amazon.rerank-v1:0"
_FORBIDDEN_MARKERS = ("cohere", "rerank-v3-5", "rerank-3-5", "opensearch")


def rerank_model_allowed(model_id: str | None) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    if any(marker in mid for marker in _FORBIDDEN_MARKERS):
        return False
    return mid == AMAZON_RERANK_MODEL_ID


def resolved_rerank_model() -> str | None:
    """Return amazon.rerank-v1:0 or None. Never returns a Cohere / OpenSearch id."""
    model_id = (settings.rerank_model_id or "").strip()
    if not model_id:
        return None
    if not rerank_model_allowed(model_id):
        logger.warning("Rerank model %s is not allowed; skipping rerank", model_id)
        return None
    return AMAZON_RERANK_MODEL_ID


def amazon_rerank(
    query: str,
    hits: list[dict],
    *,
    top_n: int,
) -> list[dict] | None:
    """Reorder hits with Amazon Rerank 1.0. Returns None if rerank is skipped or fails."""
    if not hits or not query.strip():
        return None
    if not settings.rag_rerank:
        return None
    model_id = resolved_rerank_model()
    if model_id is None:
        return None

    region = (settings.rerank_region or "us-west-2").strip() or "us-west-2"
    if region == "us-east-1":
        # Amazon Rerank 1.0 is not available in us-east-1 (only Cohere 3.5 is).
        region = "us-west-2"

    capped = hits[: min(len(hits), 25)]
    texts = [str(h.get("text") or "")[:4000] for h in capped]
    n = max(1, min(int(top_n), len(capped)))

    try:
        ranked = _rerank_via_agent_runtime(query, texts, n, region, model_id)
    except Exception:
        logger.exception("Amazon Rerank API failed; trying InvokeModel")
        try:
            ranked = _rerank_via_invoke_model(query, texts, n, region, model_id)
        except Exception:
            logger.exception("Amazon Rerank InvokeModel failed; using unre-ranked hits")
            return None

    if not ranked:
        return None

    out: list[dict] = []
    for item in ranked:
        idx = int(item["index"])
        if idx < 0 or idx >= len(capped):
            continue
        hit = dict(capped[idx])
        score = float(item["score"])
        hit["rerank_score"] = score
        hit["score"] = score
        out.append(hit)
    return out or None


def _rerank_via_agent_runtime(
    query: str,
    texts: list[str],
    top_n: int,
    region: str,
    model_id: str,
) -> list[dict]:
    import boto3

    client = boto3.client("bedrock-agent-runtime", region_name=region)
    sources = [
        {
            "type": "INLINE",
            "inlineDocumentSource": {
                "type": "TEXT",
                "textDocument": {"text": text or " "},
            },
        }
        for text in texts
    ]
    response = client.rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": query}}],
        sources=sources,
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "numberOfResults": top_n,
                "modelConfiguration": {
                    "modelArn": f"arn:aws:bedrock:{region}::foundation-model/{model_id}",
                },
            },
        },
    )
    results = []
    for row in response.get("results") or []:
        results.append(
            {
                "index": int(row["index"]),
                "score": float(row.get("relevanceScore") or 0),
            }
        )
    return results


def _rerank_via_invoke_model(
    query: str,
    texts: list[str],
    top_n: int,
    region: str,
    model_id: str,
) -> list[dict]:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=region)
    body = json.dumps({"query": query, "documents": texts, "top_n": top_n})
    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
    except ClientError:
        raise
    payload = json.loads(response["body"].read())
    raw = payload.get("results") or payload.get("ranks") or []
    results = []
    for row in raw:
        results.append(
            {
                "index": int(row.get("index", row.get("idx", 0))),
                "score": float(row.get("relevance_score") or row.get("relevanceScore") or 0),
            }
        )
    return results
