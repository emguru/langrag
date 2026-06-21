"""Offline SWAG rerank test using heuristic profiles — zero Bedrock."""

from __future__ import annotations

from ingestion.config import settings
from ingestion.swag.profile import SWAGProfileGenerator
from ingestion.swag.rerank import rerank_hits_with_swag


def offline_heuristic_rerank_smoke() -> dict:
    """Exercise full rerank path with rule-based profiles only."""
    prior_mode = settings.swag_profile_mode
    settings.swag_profile_mode = "heuristic"
    try:
        hits = [
            {
                "id": "a",
                "score": 0.72,
                "text": (
                    "Retrieval augmented generation (RAG) combines dense retrieval "
                    "with large language models for knowledge-grounded answers."
                ),
                "source": "demo/rag-guide.md",
            },
            {
                "id": "b",
                "score": 0.74,
                "text": "Configure SMTP mail relay for outbound email delivery.",
                "source": "demo/mail.md",
            },
        ]
        reranked, meta = rerank_hits_with_swag(
            "What is retrieval augmented generation?",
            hits,
            top_k=2,
        )
        top = reranked[0] if reranked else {}
        rag_won = top.get("id") == "a"
        return {
            "passed": rag_won and (top.get("swag_score") or 0) > 0,
            "top_id": top.get("id"),
            "top_swag_score": round(top.get("swag_score") or 0, 4),
            "profiles_generated": meta.get("profiles_generated"),
            "profile_mode": "heuristic",
        }
    finally:
        settings.swag_profile_mode = prior_mode
