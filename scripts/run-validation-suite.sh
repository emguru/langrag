#!/usr/bin/env bash
# Qrel-based SWAG validation (tuning + holdout suites).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH="${ROOT}/ingestion/benchmarks"
SCRIPT="${BENCH}/validation_suite.py"
EVAL="${BENCH}/validation_eval.py"
TUNING="${BENCH}/validation_qrels.json"
HOLDOUT="${BENCH}/holdout_qrels.json"
SUITE="${SUITE:-all}"

if kubectl get deploy/ingestion -n rag >/dev/null 2>&1; then
  POD="$(kubectl get pod -n rag -l app=ingestion -o jsonpath='{.items[0].metadata.name}')"
  kubectl cp "${SCRIPT}" "rag/${POD}:/tmp/validation_suite.py"
  kubectl cp "${EVAL}" "rag/${POD}:/tmp/validation_eval.py"
  kubectl cp "${TUNING}" "rag/${POD}:/tmp/validation_qrels.json"
  kubectl cp "${HOLDOUT}" "rag/${POD}:/tmp/holdout_qrels.json"
  kubectl exec -n rag deploy/ingestion -- python3 /tmp/validation_suite.py \
    --suite "${SUITE}" \
    --tuning /tmp/validation_qrels.json \
    --holdout /tmp/holdout_qrels.json
else
  PYTHONPATH="${BENCH}:${ROOT}/ingestion/src" python3 "${SCRIPT}" \
    --ingestion-url "${INGESTION_API_URL:-http://127.0.0.1:8001}" \
    --suite "${SUITE}" \
    --tuning "${TUNING}" \
    --holdout "${HOLDOUT}"
fi
