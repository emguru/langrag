from __future__ import annotations

import logging

from ingestion.config import settings
from ingestion.rag_store import persist_swag_profile
from ingestion.swag.profile import SWAGProfileGenerator, profile_from_payload
from ingestion.swag.schemas import SWAGProfile

logger = logging.getLogger(__name__)


def _profile_ready(profile: SWAGProfile | None) -> bool:
    return profile is not None and profile.confidence > 0


def ensure_swag_profiles(
    hits: list[dict],
    *,
    collection: str,
) -> tuple[list[dict], dict]:
    """
    Ensure candidate chunks have SWAG profiles.

    Missing profiles are generated inline and persisted to Qdrant so the user
    never has to re-index or run a separate profiling step.
    """
    if not hits:
        return [], {"profiles_generated": 0, "profiles_cached": 0, "profiling_ran": False}

    generator = SWAGProfileGenerator()
    enriched: list[dict] = []
    generated = 0
    cached = 0

    for hit in hits:
        profile = profile_from_payload(hit.get("swag_profile"))
        if _profile_ready(profile):
            cached += 1
            enriched.append(hit)
            continue

        source_name = str(hit.get("source", "unknown")).split("/")[-1]
        page = hit.get("page")
        page_number = int(page) if isinstance(page, int) else None
        profile = generator.generate_chunk_profile(
            str(hit.get("text") or ""),
            source_filename=source_name,
            page_number=page_number,
        )
        updated = dict(hit)
        updated["swag_profile"] = profile.model_dump()
        point_id = updated.get("id")
        if point_id and settings.swag_persist_profiles:
            try:
                persist_swag_profile(collection, str(point_id), profile.model_dump())
            except Exception as exc:
                logger.warning("Failed to persist SWAG profile for %s: %s", point_id, exc)
        generated += 1
        enriched.append(updated)

    return enriched, {
        "profiles_generated": generated,
        "profiles_cached": cached,
        "profiling_ran": generated > 0,
    }
