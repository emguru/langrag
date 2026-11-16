"""Pull public Hugging Face dataset rows via datasets-server (no heavy `datasets` dep)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
HF_SPLITS_URL = "https://datasets-server.huggingface.co/splits"
HF_DATASET_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?$")

# Prefer these column names when auto-picking text to embed.
_TEXT_FIELD_PREFERENCE = (
    "passage",
    "text",
    "context",
    "content",
    "document",
    "documents",
    "body",
    "article",
    "page_content",
    "chunk",
    "answer",
    "question",
    "prompt",
)

# Demo-friendly presets (public, text-heavy, small enough for Lambda).
HF_PRESETS: list[dict[str, Any]] = [
    {
        "id": "rag-datasets/rag-mini-wikipedia",
        "config": "text-corpus",
        "split": "passages",
        "text_fields": ["passage"],
        "id_field": "id",
        "topic": "hf-rag-wiki",
        "label": "RAG mini Wikipedia passages",
    },
    {
        "id": "neural-bridge/rag-dataset-12000",
        "config": "default",
        "split": "train",
        "text_fields": ["context"],
        "id_field": None,
        "topic": "hf-rag-neural-bridge",
        "label": "Neural Bridge RAG contexts (sample)",
    },
    {
        "id": "rajpurkar/squad",
        "config": "plain_text",
        "split": "train",
        "text_fields": ["context"],
        "id_field": "id",
        "topic": "hf-squad",
        "label": "SQuAD contexts (rajpurkar/squad)",
    },
]


def normalize_dataset_id(dataset: str) -> str:
    """Accept org/name, bare name, or a huggingface.co/datasets/... URL."""
    raw = (dataset or "").strip()
    if not raw:
        raise ValueError("dataset is required")

    if "huggingface.co" in raw:
        path = urlparse(raw if "://" in raw else f"https://{raw}").path
        parts = [unquote(p) for p in path.split("/") if p]
        if "datasets" in parts:
            i = parts.index("datasets")
            parts = parts[i + 1 :]
        if len(parts) >= 2:
            raw = f"{parts[0]}/{parts[1]}"
        elif parts:
            raw = parts[0]

    raw = raw.strip().strip("/")
    if raw.startswith("datasets/"):
        raw = raw[len("datasets/") :]

    if not HF_DATASET_RE.match(raw):
        raise ValueError(
            "dataset must be org/name (or a huggingface.co/datasets/... URL). "
            f"Got: {dataset!r}"
        )
    return raw


def validate_dataset_id(dataset: str) -> str:
    return normalize_dataset_id(dataset)


def _row_text(row: dict[str, Any], text_fields: list[str]) -> str:
    parts: list[str] = []
    for field in text_fields:
        val = row.get(field)
        if val is None:
            continue
        if isinstance(val, list):
            # messages / chat turns
            if val and isinstance(val[0], dict):
                bits = []
                for item in val:
                    content = item.get("content") if isinstance(item, dict) else None
                    if content:
                        bits.append(str(content))
                if bits:
                    parts.append("\n".join(bits))
            else:
                parts.append("\n".join(str(x) for x in val if x))
        elif isinstance(val, dict):
            content = val.get("content") or val.get("text")
            if content:
                parts.append(str(content))
        else:
            text = str(val).strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def _guess_text_fields(keys: list[str]) -> list[str]:
    lower_map = {k.lower(): k for k in keys}
    picked: list[str] = []
    for pref in _TEXT_FIELD_PREFERENCE:
        if pref in lower_map:
            picked.append(lower_map[pref])
    if picked:
        # Prefer a single strong passage/context field when present
        for strong in ("passage", "context", "text", "content", "document"):
            if strong in lower_map:
                return [lower_map[strong]]
        return picked[:2]
    # Fall back to longest-looking string-ish names
    return [keys[0]] if keys else ["text"]


def list_splits(dataset: str, config: str | None = None) -> list[dict]:
    dataset = normalize_dataset_id(dataset)
    params: dict[str, str] = {"dataset": dataset}
    if config:
        params["config"] = config
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(HF_SPLITS_URL, params=params)
        if resp.status_code >= 400:
            detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text[:300]}
            err = detail.get("error") or detail.get("detail") or resp.text[:300]
            raise RuntimeError(
                f"Hugging Face could not list splits for {dataset!r}: {err}. "
                "Use the exact repo id from the dataset page (often org/name). "
                "Legacy names like 'squad' may need 'rajpurkar/squad'."
            )
        data = resp.json()
    return list(data.get("splits") or [])


_PREFER_SPLITS = ("train", "passages", "train_sft", "validation", "test")


def _split_pair(entry: dict) -> tuple[str, str]:
    return str(entry.get("config") or "default"), str(entry.get("split") or "")


def _auto_pick_config_split(splits: list[dict]) -> tuple[str, str]:
    configs = {str(s.get("config") or "default") for s in splits}
    for pref_split in _PREFER_SPLITS:
        for s in splits:
            config, split = _split_pair(s)
            if split == pref_split and (config == "default" or len(configs) == 1):
                return config, split
    for pref_split in _PREFER_SPLITS:
        for s in splits:
            config, split = _split_pair(s)
            if split == pref_split:
                return config, split
    return _split_pair(splits[0])


def resolve_config_split(
    dataset: str,
    config: str | None,
    split: str | None,
) -> tuple[str, str, list[dict]]:
    """Pick a valid config/split. Ignore leftover values from another dataset."""
    splits = list_splits(dataset)
    if not splits:
        raise RuntimeError(
            f"No viewer-ready splits for {dataset!r}. "
            "The dataset may be gated, private, still processing, or unsupported by datasets-server."
        )

    configs = {str(s.get("config") or "default") for s in splits}
    pairs = {_split_pair(s) for s in splits}
    wanted_config = (config or "").strip() or None
    wanted_split = (split or "").strip() or None

    # Generic UI defaults are hints, not hard requirements.
    if wanted_config == "default" and "default" not in configs:
        wanted_config = None
    if wanted_split == "train" and not any(split_name == "train" for _, split_name in pairs):
        wanted_split = None

    if wanted_config and wanted_split and (wanted_config, wanted_split) in pairs:
        return wanted_config, wanted_split, splits

    if wanted_config and wanted_config in configs:
        for s in splits:
            config_name, split_name = _split_pair(s)
            if config_name == wanted_config and (not wanted_split or split_name == wanted_split):
                return config_name, split_name, splits
        config_splits = [s for s in splits if _split_pair(s)[0] == wanted_config]
        picked_config, picked_split = _auto_pick_config_split(config_splits)
        return picked_config, picked_split, splits

    # Leftover config from a previous preset/dataset — keep split if it exists here.
    if wanted_split:
        for s in splits:
            config_name, split_name = _split_pair(s)
            if split_name == wanted_split:
                return config_name, split_name, splits

    picked_config, picked_split = _auto_pick_config_split(splits)
    return picked_config, picked_split, splits


def fetch_hf_rows(
    *,
    dataset: str,
    config: str = "",
    split: str = "",
    offset: int = 0,
    length: int = 50,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (rows, feature_names)."""
    dataset = normalize_dataset_id(dataset)
    length = max(1, min(int(length), 100))
    offset = max(0, int(offset))
    params = {
        "dataset": dataset,
        "config": config or "default",
        "split": split or "train",
        "offset": offset,
        "length": length,
    }
    with httpx.Client(timeout=90.0) as client:
        resp = client.get(HF_ROWS_URL, params=params)
        if resp.status_code >= 400:
            detail = resp.text[:500]
            try:
                err_obj = resp.json()
                detail = err_obj.get("error") or err_obj.get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(
                f"Hugging Face rows error {resp.status_code} for "
                f"{dataset} config={config!r} split={split!r}: {detail}. "
                "Tip: click Look up in the UI — many datasets are not config=default / split=train "
                "(e.g. rag-mini-wikipedia uses text-corpus/passages)."
            )
        data = resp.json()

    features = [f.get("name") for f in (data.get("features") or []) if f.get("name")]
    rows_out: list[dict[str, Any]] = []
    for item in data.get("rows") or []:
        row = item.get("row") if isinstance(item, dict) else None
        if isinstance(row, dict):
            rows_out.append(row)
            if not features:
                features = list(row.keys())
    return rows_out, features


def inspect_dataset(dataset: str, config: str | None = None, split: str | None = None) -> dict:
    """Resolve config/split and sample columns for the UI Look-up flow."""
    dataset = normalize_dataset_id(dataset)
    requested_config = (config or "").strip() or None
    resolved_config, resolved_split, splits = resolve_config_split(dataset, config, split)
    options = [
        {"config": str(s.get("config") or "default"), "split": str(s.get("split") or "")}
        for s in splits
    ]

    def inspect_one(cfg: str, spl: str) -> dict:
        rows, features = fetch_hf_rows(
            dataset=dataset,
            config=cfg,
            split=spl,
            offset=0,
            length=1,
        )
        sample_keys = features or (list(rows[0].keys()) if rows else [])
        suggested = _guess_text_fields(sample_keys)
        preview = _row_text(rows[0], suggested)[:280] if rows else ""
        return {
            "dataset": dataset,
            "config": cfg,
            "split": spl,
            "available": options,
            "fields": sample_keys,
            "suggested_text_fields": suggested,
            "sample_preview": preview,
        }

    primary = inspect_one(resolved_config, resolved_split)
    honored_request = bool(requested_config and requested_config == resolved_config)
    if primary["sample_preview"] or honored_request:
        return primary

    for option in options:
        if option["config"] == resolved_config and option["split"] == resolved_split:
            continue
        candidate = inspect_one(option["config"], option["split"])
        if candidate["sample_preview"]:
            return candidate
    return primary


def ingest_huggingface(
    *,
    dataset: str,
    topic: str,
    collection: str,
    config: str = "",
    split: str = "",
    text_fields: list[str] | None = None,
    id_field: str | None = None,
    max_rows: int = 40,
    offset: int = 0,
    auto_resolve: bool = True,
) -> dict:
    """Fetch HF rows and ingest each as a text document into Qdrant + S3."""
    from ingestion.pipeline import ingest_bytes

    dataset = normalize_dataset_id(dataset)
    if not topic.strip():
        raise ValueError("topic required")

    requested_config, requested_split = config, split
    if auto_resolve:
        config, split, _ = resolve_config_split(dataset, config, split)

    rows, features = fetch_hf_rows(
        dataset=dataset,
        config=config,
        split=split,
        offset=offset,
        length=max_rows,
    )

    fields = [f.strip() for f in (text_fields or []) if f.strip()]
    if not fields:
        fields = _guess_text_fields(features or (list(rows[0].keys()) if rows else []))
    # If user fields miss entirely, fall back to guess
    if rows and not any(f in rows[0] for f in fields):
        fields = _guess_text_fields(list(rows[0].keys()))

    documents: list[dict] = []
    skipped = 0
    total_chunks = 0

    for i, row in enumerate(rows):
        text = _row_text(row, fields)
        if not text or len(text) < 40:
            skipped += 1
            continue
        row_id = row.get(id_field) if id_field else None
        if row_id is None:
            row_id = row.get("id") or row.get("_id") or row.get("prompt_id") or f"{offset + i}"
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(row_id))[:80]
        filename = f"{safe_id}.md"
        body = (
            f"# Hugging Face · {dataset}\n"
            f"config={config} split={split} row={row_id}\n\n"
            f"{text}"
        ).encode("utf-8")
        result = ingest_bytes(body, filename, topic, collection)
        total_chunks += int(result.get("chunks_indexed") or 0)
        documents.append(
            {
                "document_id": result["document_id"],
                "source": result["source"],
                "chunks_indexed": result["chunks_indexed"],
                "hf_row_id": str(row_id),
            }
        )

    return {
        "dataset": dataset,
        "config": config,
        "split": split,
        "requested_config": requested_config,
        "requested_split": requested_split,
        "text_fields": fields,
        "topic": topic,
        "collection": collection,
        "rows_fetched": len(rows),
        "documents_indexed": len(documents),
        "rows_skipped": skipped,
        "chunks_indexed": total_chunks,
        "documents": documents,
    }
