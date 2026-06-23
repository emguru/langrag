#!/usr/bin/env bash
# Seed research_docs with public URLs so you can test RAG/SWAG/CRAG without owning PDFs.
# Set SWAG_PROFILE_MODE=heuristic on ingestion (ConfigMap) so ingest skips Bedrock profiling.
#
# RECREATE=1 wipes the collection (destructive). Default append mode keeps existing chunks
# and adds new URLs — use this when growing a diverse corpus in Pass 2+.
set -euo pipefail

TOPIC="${TOPIC:-rag-demo}"
COLLECTION="${COLLECTION:-research_docs}"
INGEST="${INGEST:-http://127.0.0.1:8001}"
RECREATE="${RECREATE:-0}"

URLS=(
  "https://raw.githubusercontent.com/langchain-ai/rag-from-scratch/main/README.md"
  "https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/generative-ai-rag.html"
  "https://raw.githubusercontent.com/microsoft/generative-ai-for-beginners/main/08-building-search-applications/README.md"
  "https://raw.githubusercontent.com/microsoft/generative-ai-for-beginners/main/04-prompt-engineering-fundamentals/README.md"
  "https://raw.githubusercontent.com/microsoft/generative-ai-for-beginners/main/06-text-generation-apps/README.md"
)

run_ingest() {
  local url="$1"
  echo "Ingesting: ${url}"
  SEED_URL="${url}" SEED_TOPIC="${TOPIC}" SEED_COLLECTION="${COLLECTION}" SEED_INGEST="${INGEST}" \
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

if kubectl get deploy/ingestion -n rag >/dev/null 2>&1; then
  if [[ "${RECREATE}" == "1" ]]; then
    echo "Recreating collection ${COLLECTION} (delete + re-ingest all URLs)..."
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
  fi
  for url in "${URLS[@]}"; do
    kubectl exec -n rag deploy/ingestion -- python3 -c "
import httpx
url = '${url}'
print('Ingesting:', url, flush=True)
r = httpx.post('http://127.0.0.1:8001/ingest/url', json={'url': url, 'topic': '${TOPIC}', 'collection': '${COLLECTION}'}, timeout=3600)
print(r.status_code, r.text)
r.raise_for_status()
data = r.json()
print('  ->', data.get('chunks_indexed'), 'chunks', data.get('document_id'))
"
  done
  kubectl exec -n rag deploy/ingestion -- python3 -c "
from qdrant_client import QdrantClient
print('points:', QdrantClient(url='http://qdrant.rag.svc.cluster.local:6333').get_collection('${COLLECTION}').points_count)
"
else
  echo "Local mode: set INGEST (e.g. http://localhost:8001) and run ingestion API reachable." >&2
  for url in "${URLS[@]}"; do
    run_ingest "${url}"
  done
fi

echo "Done. Search tab / research topic ideas:"
echo "  - What is retrieval augmented generation?"
echo "  - How does chunking affect RAG quality?"
echo "  - Compare naive RAG vs corrective RAG approaches"
