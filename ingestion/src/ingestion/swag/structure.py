"""Structure-aware markdown segmentation — split nav/title from body before chunking."""

from __future__ import annotations

import re

from ingestion.swag.chunk_quality import classify_chunk_kind

_HEADING_SPLIT = re.compile(r"(?=^#{1,3}\s)", re.MULTILINE)


def split_markdown_segments(text: str) -> list[tuple[str, str]]:
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    parts = [part.strip() for part in _HEADING_SPLIT.split(cleaned) if part.strip()]
    if not parts:
        return [(cleaned, classify_chunk_kind(cleaned))]

    return [(part, classify_chunk_kind(part)) for part in parts]
