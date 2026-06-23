#!/usr/bin/env bash
# Batch-seed diverse corpora into research_docs (append-only by default).
# Ingest path: fetch URL -> chunk -> Titan embed -> heuristic SWAG profile (no LLM).
#
# Usage:
#   ./scripts/seed-corpus-batch.sh
#   DRY_RUN=1 ./scripts/seed-corpus-batch.sh
#   TOPICS=ap-procurement,cafeteria-ops ./scripts/seed-corpus-batch.sh
#   MANIFEST=./my.manifest ./scripts/seed-corpus-batch.sh
#   RECREATE=1 ./scripts/seed-corpus-batch.sh   # destructive — wipes collection first
#
# Env:
#   COLLECTION          Qdrant collection (default: research_docs)
#   INGEST              Local ingestion API when cluster not running (default: http://127.0.0.1:8001)
#   RECREATE            0 append (default), 1 delete + recreate collection before ingest
#   DRY_RUN             1 = plan + Bedrock cost estimate only
#   SKIP_EXISTING       1 = skip topic|url rows already in scripts/.seed-corpus-batch.state
#   TOPICS              Comma-separated topic filter (e.g. ap-procurement,cafeteria-ops)
#   CONTINUE_ON_ERROR   1 = keep going after a failed URL (default: 0)
#   CHUNKS_PER_URL      Cost estimate assumption (default: 22, from demo corpus avg)
#   TOKENS_PER_CHUNK    Cost estimate assumption (default: 250)
#   TITAN_USD_PER_1K    Titan Embed v2 list price us-east-1 (default: 0.00002)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${MANIFEST:-${ROOT}/scripts/corpus-batch.manifest}"
STATE_FILE="${STATE_FILE:-${ROOT}/scripts/.seed-corpus-batch.state}"
COLLECTION="${COLLECTION:-research_docs}"
INGEST="${INGEST:-http://127.0.0.1:8001}"
RECREATE="${RECREATE:-0}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_EXISTING="${SKIP_EXISTING:-1}"
CONTINUE_ON_ERROR="${CONTINUE_ON_ERROR:-0}"
CHUNKS_PER_URL="${CHUNKS_PER_URL:-22}"
TOKENS_PER_CHUNK="${TOKENS_PER_CHUNK:-250}"
TITAN_USD_PER_1K="${TITAN_USD_PER_1K:-0.00002}"
TOPICS="${TOPICS:-}"

if [[ ! -f "${MANIFEST}" ]]; then
  echo "Manifest not found: ${MANIFEST}" >&2
  exit 1
fi

topic_allowed() {
  local topic="$1"
  if [[ -z "${TOPICS}" ]]; then
    return 0
  fi
  local IFS=,
  local t
  for t in ${TOPICS}; do
    if [[ "${t}" == "${topic}" ]]; then
      return 0
    fi
  done
  return 1
}

state_has() {
  local key="$1"
  [[ -f "${STATE_FILE}" ]] && grep -Fxq "${key}" "${STATE_FILE}"
}

load_entries() {
  local quiet="${1:-0}"
  local line topic url key
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "${line}" ]] && continue
    topic="${line%%|*}"
    url="${line#*|}"
    if [[ -z "${topic}" || -z "${url}" || "${topic}" == "${url}" ]]; then
      echo "Skipping malformed manifest line: ${line}" >&2
      continue
    fi
    if ! topic_allowed "${topic}"; then
      continue
    fi
    key="${topic}|${url}"
    if [[ "${SKIP_EXISTING}" == "1" ]] && state_has "${key}"; then
      [[ "${quiet}" == "0" ]] && echo "SKIP (already seeded): ${key}"
      continue
    fi
    printf '%s\t%s\n' "${topic}" "${url}"
  done < "${MANIFEST}"
}

estimate_embed_usd() {
  local urls="$1"
  python3 - "${urls}" "${CHUNKS_PER_URL}" "${TOKENS_PER_CHUNK}" "${TITAN_USD_PER_1K}" <<'PY'
import sys

urls = int(sys.argv[1])
chunks_per = int(sys.argv[2])
tokens_per = int(sys.argv[3])
price = float(sys.argv[4])
chunks = urls * chunks_per
tokens = chunks * tokens_per
usd = (tokens / 1000.0) * price
print(f"{chunks}\t{tokens}\t{usd:.6f}")
PY
}

print_plan() {
  local entries count topics_line
  entries="$(load_entries 1)"
  if [[ -z "${entries}" ]]; then
    echo "No URLs to ingest (empty after filters / SKIP_EXISTING)."
    if [[ -f "${STATE_FILE}" ]]; then
      echo "State file: ${STATE_FILE} ($(wc -l < "${STATE_FILE}" | tr -d ' ') rows)"
    fi
    return 0
  fi

  count="$(printf '%s\n' "${entries}" | wc -l | tr -d ' ')"
  topics_line="$(printf '%s\n' "${entries}" | cut -f1 | sort -u | paste -sd, -)"

  echo "=== Corpus batch plan ==="
  echo "Manifest:    ${MANIFEST}"
  echo "Collection:  ${COLLECTION}"
  echo "Mode:        $([[ "${RECREATE}" == "1" ]] && echo 'RECREATE (wipe collection)' || echo 'append (keep existing points)')"
  echo "URLs:        ${count}"
  echo "Topics:      ${topics_line}"
  echo "SWAG ingest: heuristic profiles (no Bedrock LLM; set SWAG_PROFILE_MODE=heuristic)"
  echo
  printf '%s\n' "${entries}" | while IFS=$'\t' read -r topic url; do
    echo "  [${topic}] ${url}"
  done
  echo

  read -r est_chunks est_tokens est_usd < <(estimate_embed_usd "${count}")
  echo "=== Bedrock cost estimate (embed only) ==="
  echo "Assumed chunks/URL: ${CHUNKS_PER_URL}"
  echo "Assumed tokens/chunk: ${TOKENS_PER_CHUNK}"
  echo "Estimated chunks:     ${est_chunks}"
  echo "Estimated tokens:     ${est_tokens}"
  echo "Estimated Titan USD:  \$${est_usd}"
  echo "(Cluster compute while ingesting is separate; run demo-stop when done.)"
}

recreate_collection_k8s() {
  kubectl exec -n rag deploy/ingestion -- python3 -c "
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

client = QdrantClient(url='http://qdrant.rag.svc.cluster.local:6333')
collection = '${COLLECTION}'
try:
    client.delete_collection(collection)
    print('deleted collection', collection)
except Exception as exc:
    print('delete skipped:', exc)
client.create_collection(
    collection_name=collection,
    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
)
print('created empty collection', collection)
"
}

ingest_url_local() {
  local topic="$1"
  local url="$2"
  SEED_URL="${url}" SEED_TOPIC="${topic}" SEED_COLLECTION="${COLLECTION}" SEED_INGEST="${INGEST}" \
    python3 - <<'PY'
import os
import httpx

resp = httpx.post(
    f"{os.environ['SEED_INGEST']}/ingest/url",
    json={
        "url": os.environ["SEED_URL"],
        "topic": os.environ["SEED_TOPIC"],
        "collection": os.environ["SEED_COLLECTION"],
    },
    timeout=3600,
)
print(resp.status_code, resp.text)
resp.raise_for_status()
data = resp.json()
print(f"  -> {data['chunks_indexed']} chunks ({data['document_id']})")
PY
}

ingest_url_k8s() {
  local topic="$1"
  local url="$2"
  kubectl exec -n rag deploy/ingestion -- python3 -c "
import httpx
url = '''${url}'''
topic = '''${topic}'''
collection = '''${COLLECTION}'''
print('Ingesting:', url, flush=True)
r = httpx.post(
    'http://127.0.0.1:8001/ingest/url',
    json={'url': url, 'topic': topic, 'collection': collection},
    timeout=3600,
)
print(r.status_code, r.text)
r.raise_for_status()
data = r.json()
print('  ->', data.get('chunks_indexed'), 'chunks', data.get('document_id'))
"
}

collection_points_k8s() {
  kubectl exec -n rag deploy/ingestion -- python3 -c "
from qdrant_client import QdrantClient
print(QdrantClient(url='http://qdrant.rag.svc.cluster.local:6333').get_collection('${COLLECTION}').points_count)
"
}

run_batch() {
  local entries topic url key
  local ok=0 fail=0 skipped=0 chunks=0
  entries="$(load_entries || true)"

  if [[ -z "${entries}" ]]; then
    echo "Nothing to do."
    return 0
  fi

  mkdir -p "$(dirname "${STATE_FILE}")"
  touch "${STATE_FILE}"

  if kubectl get deploy/ingestion -n rag >/dev/null 2>&1; then
    if [[ "${RECREATE}" == "1" ]]; then
      echo "Recreating collection ${COLLECTION}..."
      recreate_collection_k8s
    fi
    while IFS=$'\t' read -r topic url; do
      key="${topic}|${url}"
      echo "Ingesting [${topic}]: ${url}"
      if ingest_url_k8s "${topic}" "${url}"; then
        ok=$((ok + 1))
        echo "${key}" >> "${STATE_FILE}"
      else
        fail=$((fail + 1))
        [[ "${CONTINUE_ON_ERROR}" == "1" ]] || exit 1
      fi
    done <<< "${entries}"
    echo "Qdrant points in ${COLLECTION}: $(collection_points_k8s)"
  else
    echo "Local mode: INGEST=${INGEST}" >&2
    while IFS=$'\t' read -r topic url; do
      key="${topic}|${url}"
      echo "Ingesting [${topic}]: ${url}"
      if ingest_url_local "${topic}" "${url}"; then
        ok=$((ok + 1))
        echo "${key}" >> "${STATE_FILE}"
      else
        fail=$((fail + 1))
        [[ "${CONTINUE_ON_ERROR}" == "1" ]] || exit 1
      fi
    done <<< "${entries}"
  fi

  echo
  echo "Batch complete: ${ok} ingested, ${fail} failed."
  echo "State file: ${STATE_FILE}"
  echo
  echo "Sample cross-topic queries (mixed-corpus disambiguation):"
  echo "  - How do we automate invoice processing and PO approval?"
  echo "  - What are food safety basics for cafeteria operations?"
  echo "  - Compare RAG chunking strategies for retrieval quality"
}

print_plan

if [[ "${DRY_RUN}" == "1" ]]; then
  echo
  echo "DRY_RUN=1 — no ingest performed."
  exit 0
fi

run_batch
