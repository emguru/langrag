#!/usr/bin/env bash
# Seed the validation corpus (fixtures + manifest URLs) with stable seed_key labels.
# Wipes research_docs when RECREATE=1 (default for honest re-proof).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${MANIFEST:-${ROOT}/ingestion/benchmarks/validation_manifest.json}"
FIXTURES="${FIXTURES:-${ROOT}/fixtures/corpus}"
SEED_PY="${ROOT}/ingestion/benchmarks/seed_validation_corpus.py"
COLLECTION="${COLLECTION:-research_docs}"
RECREATE="${RECREATE:-1}"
SKIP_URLS="${SKIP_URLS:-0}"

if kubectl get deploy/ingestion -n rag >/dev/null 2>&1; then
  POD="$(kubectl get pod -n rag -l app=ingestion -o jsonpath='{.items[0].metadata.name}')"
  echo "==> Copying fixtures and seeder to ingestion pod"
  kubectl exec -n rag deploy/ingestion -- rm -rf /tmp/fixtures /tmp/seed_validation_corpus.py
  kubectl cp "${FIXTURES}" "rag/${POD}:/tmp/fixtures"
  kubectl cp "${MANIFEST}" "rag/${POD}:/tmp/validation_manifest.json"
  kubectl cp "${SEED_PY}" "rag/${POD}:/tmp/seed_validation_corpus.py"
  echo "==> Seeding validation corpus (RECREATE=${RECREATE})"
  kubectl exec -n rag deploy/ingestion -- python3 /tmp/seed_validation_corpus.py \
    --manifest /tmp/validation_manifest.json \
    --fixtures-root /tmp/fixtures \
    --collection "${COLLECTION}" \
    --qdrant-url http://qdrant.rag.svc.cluster.local:6333 \
    $([[ "${RECREATE}" == "1" ]] && echo --recreate) \
    $([[ "${SKIP_URLS}" == "1" ]] && echo --skip-urls)
else
  echo "Local seed (requires Qdrant + Bedrock reachable from this machine)"
  PYTHONPATH="${ROOT}/ingestion/src" python3 "${SEED_PY}" \
    --manifest "${MANIFEST}" \
    --fixtures-root "${FIXTURES}" \
    --collection "${COLLECTION}" \
    $([[ "${RECREATE}" == "1" ]] && echo --recreate) \
    $([[ "${SKIP_URLS}" == "1" ]] && echo --skip-urls)
fi

echo "==> Done. Run: ./scripts/run-validation-suite.sh"
