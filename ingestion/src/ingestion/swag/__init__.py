"""SWAG — Semantic Weighted Augmented Generation (reranking layer)."""

from ingestion.swag.rerank import rerank_hits_with_swag
from ingestion.swag.profile import SWAGProfileGenerator

__all__ = ["rerank_hits_with_swag", "SWAGProfileGenerator"]
