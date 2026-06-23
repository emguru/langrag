"""Pull sibling chunks from the same document when one chunk passes filtering."""

from __future__ import annotations

from ingestion.config import settings
from ingestion.swag.chunk_quality import chunk_kind_from_profile, classify_chunk_kind
from ingestion.swag.postprocess import source_group_key
from ingestion.swag.profile import profile_from_payload

_KIND_RANK = {"body": 0, "title": 1, "nav": 2, "boilerplate": 3, "empty": 4}
_EXPAND_SKIP_KINDS = frozenset({"empty", "boilerplate", "title", "nav"})


def _hit_key(hit: dict) -> str:
    return str(
        hit.get("id")
        or hit.get("document_id")
        or f"{hit.get('source', '')}:{hit.get('page', '')}:{hit.get('chunk_index', '')}"
    )


def _chunk_kind(hit: dict) -> str:
    kind = hit.get("chunk_kind")
    if kind:
        return str(kind)
    profile = profile_from_payload(hit.get("swag_profile"))
    return chunk_kind_from_profile(profile, text=str(hit.get("text") or ""))


def _chunk_order_key(hit: dict) -> tuple:
    kind = _chunk_kind(hit)
    index = int(hit.get("chunk_index") if hit.get("chunk_index") is not None else 0)
    return (index, _KIND_RANK.get(kind, 1), str(hit.get("id") or ""))


def expand_same_document_siblings(
    kept: list[dict],
    pool: list[dict],
    *,
    top_k: int | None = None,
) -> tuple[list[dict], dict]:
    """
    When a chunk survives filtering, add other chunks from the same document
    that appear in the retrieval pool (reading order, lightweight kind filter).
    """
    if not settings.rag_expand_same_document or not kept or not pool:
        return kept, {"enabled": False, "siblings_added": 0}

    max_per_doc = settings.rag_same_document_max_per_doc
    limit = top_k or settings.rag_top_k
    max_total = limit

    docs_ordered: list[str] = []
    for hit in kept:
        doc_key = source_group_key(hit)
        if doc_key not in docs_ordered:
            docs_ordered.append(doc_key)

    pool_by_doc: dict[str, list[dict]] = {}
    for hit in pool:
        pool_by_doc.setdefault(source_group_key(hit), []).append(hit)

    result: list[dict] = []
    seen: set[str] = set()
    siblings_added = 0
    docs_expanded: set[str] = set()

    for hit in kept:
        key = _hit_key(hit)
        doc_key = source_group_key(hit)
        if key not in seen:
            enriched = dict(hit)
            enriched["chunk_kind"] = _chunk_kind(hit)
            result.append(enriched)
            seen.add(key)

        if doc_key in docs_expanded:
            continue
        docs_expanded.add(doc_key)
        per_doc = sum(1 for item in result if source_group_key(item) == doc_key)
        for sibling in sorted(pool_by_doc.get(doc_key, []), key=_chunk_order_key):
            skey = _hit_key(sibling)
            if skey in seen:
                continue
            if per_doc >= max_per_doc:
                break
            kind = _chunk_kind(sibling)
            if kind in _EXPAND_SKIP_KINDS:
                continue

            enriched = dict(sibling)
            enriched["chunk_kind"] = kind
            enriched["expanded_sibling"] = True
            result.append(enriched)
            seen.add(skey)
            per_doc += 1
            siblings_added += 1
            if len(result) >= max_total:
                break
        if len(result) >= max_total:
            break

    for hit in kept:
        key = _hit_key(hit)
        if key not in seen:
            result.append(dict(hit))
            seen.add(key)

    return result[:max_total], {
        "enabled": True,
        "siblings_added": siblings_added,
        "documents_expanded": len(docs_ordered),
    }


def backfill_body_from_pool(
    hits: list[dict],
    pool: list[dict],
    *,
    top_k: int | None = None,
) -> tuple[list[dict], dict]:
    """When body-only pruning leaves fewer than top_k, add body chunks from the pool."""
    limit = top_k or settings.rag_top_k
    if len(hits) >= limit or not pool:
        return hits[:limit], {"backfilled": 0, "enabled": False}

    seen = {_hit_key(hit) for hit in hits}
    candidates = [
        hit
        for hit in pool
        if _chunk_kind(hit) == "body" and _hit_key(hit) not in seen
    ]
    candidates.sort(key=lambda hit: float(hit.get("score") or 0.0), reverse=True)

    result = list(hits)
    added = 0
    for hit in candidates:
        if len(result) >= limit:
            break
        enriched = dict(hit)
        enriched["chunk_kind"] = _chunk_kind(hit)
        enriched["backfilled_body"] = True
        result.append(enriched)
        seen.add(_hit_key(hit))
        added += 1

    return result, {"enabled": added > 0, "backfilled": added}


def prune_low_value_hits(hits: list[dict], *, top_k: int | None = None) -> tuple[list[dict], dict]:
    """Drop title/nav/boilerplate chunks from search results; prefer substantive body."""
    if not settings.rag_filter_non_body_hits or not hits:
        return hits, {"filtered": 0, "enabled": False}

    limit = top_k or settings.rag_top_k

    def _kind(hit: dict) -> str:
        return _chunk_kind(hit)

    substantive = [h for h in hits if _kind(h) == "body"]
    if substantive:
        pruned = substantive[:limit]
        return pruned, {
            "enabled": True,
            "filtered": len(hits) - len(pruned),
            "kept_kind": "body",
        }

    return hits[:limit], {"enabled": True, "filtered": 0, "kept_kind": "fallback"}
