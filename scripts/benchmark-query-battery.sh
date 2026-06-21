#!/usr/bin/env bash
# Baseline vs SWAG on 10 fixed queries + scored eval sheet (~20 Titan embeds).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH="${ROOT}/ingestion/benchmarks"
SCRIPT="${BENCH}/query_battery.py"
EVAL="${BENCH}/query_eval.py"
CASES="${BENCH}/query_eval_cases.json"

if kubectl get deploy/ingestion -n rag >/dev/null 2>&1; then
  POD="$(kubectl get pod -n rag -l app=ingestion -o jsonpath='{.items[0].metadata.name}')"
  kubectl cp "${SCRIPT}" "rag/${POD}:/tmp/query_battery.py"
  kubectl cp "${EVAL}" "rag/${POD}:/tmp/query_eval.py"
  kubectl cp "${CASES}" "rag/${POD}:/tmp/query_eval_cases.json"
  kubectl exec -n rag deploy/ingestion -- python3 /tmp/query_battery.py --cases /tmp/query_eval_cases.json
else
  python3 "${SCRIPT}" \
    --ingestion-url "${INGESTION_API_URL:-http://127.0.0.1:8001}" \
    --cases "${CASES}"
fi
