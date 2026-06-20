from __future__ import annotations

import io

from pypdf import PdfReader

from ingestion.pipeline import chunk_documents, extract_text_from_bytes


def preview_bytes(data: bytes, filename: str, topic: str, preview_limit: int = 25) -> dict:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    pdf_page_count = None
    if suffix == "pdf":
        reader = PdfReader(io.BytesIO(data))
        pdf_page_count = len(reader.pages)

    pages = extract_text_from_bytes(data, filename)
    total_chars = sum(len(text) for text, _ in pages)
    preview_source = f"preview://{topic}/{filename}"
    chunks = chunk_documents(pages, source=preview_source)

    chunk_lengths = [len(c["text"]) for c in chunks]
    avg_chunk = int(sum(chunk_lengths) / len(chunk_lengths)) if chunk_lengths else 0

    return {
        "filename": filename,
        "topic": topic,
        "pdf_page_count": pdf_page_count,
        "pages_extracted": len(pages),
        "total_chars": total_chars,
        "chunk_count": len(chunks),
        "avg_chunk_chars": avg_chunk,
        "min_chunk_chars": min(chunk_lengths) if chunk_lengths else 0,
        "max_chunk_chars": max(chunk_lengths) if chunk_lengths else 0,
        "empty_extraction": total_chars == 0,
        "preview_chunks": [
            {
                "page": c["page"],
                "chunk_index": c["chunk_index"],
                "char_count": len(c["text"]),
                "text_preview": c["text"][:400],
            }
            for c in chunks[:preview_limit]
        ],
        "preview_truncated": len(chunks) > preview_limit,
    }
