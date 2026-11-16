#!/usr/bin/env bash
# Pull a Hugging Face dataset slice into Qdrant via the ingestion API.
set -euo pipefail

API="${API:-http://localhost:8001}"
DATASET="${DATASET:-rag-datasets/rag-mini-wikipedia}"
CONFIG="${CONFIG:-text-corpus}"
SPLIT="${SPLIT:-passages}"
TOPIC="${TOPIC:-hf-rag-wiki}"
TEXT_FIELDS="${TEXT_FIELDS:-passage}"
MAX_ROWS="${MAX_ROWS:-40}"
OFFSET="${OFFSET:-0}"
ID_FIELD="${ID_FIELD:-id}"

API="${API%/}"

payload=$(DATASET="$DATASET" CONFIG="$CONFIG" SPLIT="$SPLIT" TOPIC="$TOPIC" \
  TEXT_FIELDS="$TEXT_FIELDS" MAX_ROWS="$MAX_ROWS" OFFSET="$OFFSET" ID_FIELD="$ID_FIELD" \
  python3 - <<'PY'
import json, os
fields = [f.strip() for f in os.environ["TEXT_FIELDS"].split(",") if f.strip()]
print(json.dumps({
    "dataset": os.environ["DATASET"],
    "config": os.environ["CONFIG"],
    "split": os.environ["SPLIT"],
    "topic": os.environ["TOPIC"],
    "text_fields": fields,
    "id_field": os.environ.get("ID_FIELD") or None,
    "max_rows": int(os.environ["MAX_ROWS"]),
    "offset": int(os.environ["OFFSET"]),
}))
PY
)

echo "Ingesting Hugging Face · ${DATASET} split=${SPLIT} max_rows=${MAX_ROWS}"
echo "→ ${API}/ingest/huggingface"

curl -sf -X POST "${API}/ingest/huggingface" \
  -H 'Content-Type: application/json' \
  -d "$payload" | python3 -m json.tool
