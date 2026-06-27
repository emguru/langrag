"""Baseline retrievers for offline harness comparison."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


class BM25Index:
    """Lightweight Okapi BM25 over chunk texts (no extra deps)."""

    def __init__(self, docs: list[dict], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._docs = docs
        self._k1 = k1
        self._b = b
        self._doc_tokens: list[list[str]] = [tokenize(str(doc.get("text") or "")) for doc in docs]
        self._avgdl = sum(len(tokens) for tokens in self._doc_tokens) / max(len(self._doc_tokens), 1)
        self._df: dict[str, int] = {}
        for tokens in self._doc_tokens:
            for term in set(tokens):
                self._df[term] = self._df.get(term, 0) + 1
        self._N = len(docs)

    def score_query(self, query: str) -> list[tuple[float, dict]]:
        q_terms = tokenize(query)
        if not q_terms:
            return [(0.0, doc) for doc in self._docs]
        scored: list[tuple[float, dict]] = []
        for tokens, doc in zip(self._doc_tokens, self._docs):
            dl = len(tokens) or 1
            tf_map: dict[str, int] = {}
            for term in tokens:
                tf_map[term] = tf_map.get(term, 0) + 1
            total = 0.0
            for term in q_terms:
                df = self._df.get(term, 0)
                if df == 0:
                    continue
                idf = math.log(1 + (self._N - df + 0.5) / (df + 0.5))
                tf = tf_map.get(term, 0)
                denom = tf + self._k1 * (1 - self._b + self._b * dl / self._avgdl)
                total += idf * (tf * (self._k1 + 1)) / denom
            scored.append((total, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored


def _normalize_scores(scored: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
    if not scored:
        return scored
    values = [score for score, _ in scored]
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [(1.0 if idx == 0 else 0.0, doc) for idx, (_, doc) in enumerate(scored)]
    return [((score - lo) / (hi - lo), doc) for score, doc in scored]


def _strip_internal(hit: dict) -> dict:
    return {key: value for key, value in hit.items() if not key.startswith("_")}


def _attach_scores(scored: list[tuple[float, dict]], *, top_k: int) -> list[dict]:
    rows: list[dict] = []
    for score, hit in scored[:top_k]:
        row = _strip_internal(dict(hit))
        row["score"] = float(score)
        rows.append(row)
    return rows


def run_bm25(
    bm25: BM25Index,
    query: str,
    *,
    topic: str | None,
    top_k: int,
) -> list[dict]:
    scored = bm25.score_query(query)
    if topic:
        scored = [(score, hit) for score, hit in scored if str(hit.get("topic") or "") == topic]
    return _attach_scores(scored, top_k=top_k)


def run_vector(
    vector_search: Callable[..., list[dict]],
    indexed: list[dict],
    embedder,
    query: str,
    *,
    topic: str | None,
    top_k: int,
) -> list[dict]:
    hits = vector_search(indexed, embedder, query, top_k, topic=topic)
    return [_strip_internal(hit) for hit in hits]


def run_hybrid_rrf(
    bm25: BM25Index,
    vector_search: Callable[..., list[dict]],
    indexed: list[dict],
    embedder,
    query: str,
    *,
    topic: str | None,
    top_k: int,
    pool_k: int,
    k_rrf: int = 60,
) -> list[dict]:
    bm25_scored = run_bm25(bm25, query, topic=topic, top_k=pool_k)
    vector_scored = run_vector(
        vector_search, indexed, embedder, query, topic=topic, top_k=pool_k
    )
    ranks: dict[str, float] = {}

    def _key(hit: dict) -> str:
        return str(hit.get("id") or hit.get("seed_key") or hit.get("source"))

    for rank, hit in enumerate(bm25_scored, start=1):
        ranks[_key(hit)] = ranks.get(_key(hit), 0.0) + 1.0 / (k_rrf + rank)
    for rank, hit in enumerate(vector_scored, start=1):
        ranks[_key(hit)] = ranks.get(_key(hit), 0.0) + 1.0 / (k_rrf + rank)

    pool = { _key(hit): hit for hit in bm25_scored + vector_scored }
    merged = sorted(pool.items(), key=lambda item: ranks.get(item[0], 0.0), reverse=True)
    rows: list[dict] = []
    for key, hit in merged[:top_k]:
        row = dict(hit)
        row["score"] = ranks.get(key, 0.0)
        rows.append(row)
    return rows


def run_hybrid_rerank(
    bm25: BM25Index,
    vector_search: Callable[..., list[dict]],
    indexed: list[dict],
    embedder,
    query: str,
    *,
    topic: str | None,
    top_k: int,
    pool_k: int,
) -> list[dict]:
    """Vector candidate pool + BM25 re-order (strong lexical reranker baseline)."""
    pool = run_vector(
        vector_search, indexed, embedder, query, topic=topic, top_k=pool_k
    )
    if not pool:
        return []
    bm25_scored = bm25.score_query(query)
    bm25_map = {
        str(hit.get("id") or hit.get("seed_key") or hit.get("source")): score
        for score, hit in bm25_scored
    }
    reranked = sorted(
        pool,
        key=lambda hit: bm25_map.get(
            str(hit.get("id") or hit.get("seed_key") or hit.get("source")), 0.0
        ),
        reverse=True,
    )
    return reranked[:top_k]


def run_swag(
    vector_search: Callable[..., list[dict]],
    indexed: list[dict],
    embedder,
    query: str,
    *,
    topic: str | None,
    top_k: int,
    pool_k: int,
) -> list[dict]:
    from ingestion.swag.rerank import rerank_hits_with_swag

    pool = vector_search(indexed, embedder, query, pool_k, topic=topic)
    pool_clean = [_strip_internal(dict(hit)) for hit in pool]
    reranked, _meta = rerank_hits_with_swag(query, pool_clean, top_k=top_k)
    return [_strip_internal(hit) for hit in reranked]


BASELINE_RUNNERS = {
    "bm25": "bm25",
    "vector": "vector",
    "hybrid_rrf": "hybrid_rrf",
    "hybrid_rerank": "hybrid_rerank",
    "swag": "swag",
}
