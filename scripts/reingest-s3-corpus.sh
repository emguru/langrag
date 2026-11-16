#!/usr/bin/env bash
# Re-ingest documents from an S3 bucket via the ingestion HTTP API.
set -euo pipefail

SOURCE_BUCKET="${SOURCE_BUCKET:-}"
API="${API:-}"
COLLECTION="${COLLECTION:-research_docs}"
REGION="${AWS_REGION:-us-east-1}"

if [[ -z "$SOURCE_BUCKET" ]]; then
  echo "Set SOURCE_BUCKET to the S3 corpus bucket name" >&2
  exit 1
fi
if [[ -z "$API" ]]; then
  echo "Set API to the API Gateway base URL (e.g. https://xxx.execute-api.us-east-1.amazonaws.com)" >&2
  exit 1
fi

API="${API%/}"
TMPDIR="${TMPDIR:-/tmp}/langrag-reingest-$$"
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Listing s3://${SOURCE_BUCKET} ..."
KEYS=()
while IFS= read -r key; do
  [[ -n "$key" ]] && KEYS+=("$key")
done < <(aws s3api list-objects-v2 --bucket "$SOURCE_BUCKET" --query 'Contents[].Key' --output text | tr '\t' '\n')

total=${#KEYS[@]}
ok=0
fail=0

for key in "${KEYS[@]}"; do
  topic="${key%%/*}"
  filename="${key##*/}"
  local_file="${TMPDIR}/${filename}"
  echo "[$((ok + fail + 1))/${total}] ${key}"
  aws s3 cp "s3://${SOURCE_BUCKET}/${key}" "$local_file" --region "$REGION" --quiet
  resp=$(curl -sf -X POST "${API}/ingest/file" \
    -F "file=@${local_file};filename=${filename}" \
    -F "topic=${topic}" \
    -F "collection=${COLLECTION}" || true)
  if [[ -n "$resp" ]] && echo "$resp" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    chunks=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('chunks_indexed',0))")
    echo "  -> ${chunks} chunks"
    ok=$((ok + 1))
  else
    echo "  FAIL: ${resp:-curl error}" >&2
    fail=$((fail + 1))
  fi
  rm -f "$local_file"
done

echo ""
echo "Done: ${ok} ingested, ${fail} failed (of ${total})"
[[ "$fail" -eq 0 ]]
