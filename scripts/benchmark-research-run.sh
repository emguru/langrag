#!/usr/bin/env bash
set -euo pipefail

THREAD_ID="${1:-}"
if [[ -z "${THREAD_ID}" ]]; then
  echo "Usage: $0 <thread_id>" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${ROOT}/ingestion/benchmarks/research_run_benchmark.py"

if kubectl get deploy/research-agents -n agents >/dev/null 2>&1; then
  kubectl cp "${SCRIPT}" "agents/$(kubectl get pod -n agents -l app=research-agents -o jsonpath='{.items[0].metadata.name}'):/tmp/research_run_benchmark.py"
  kubectl exec -n agents deploy/research-agents -- python3 /tmp/research_run_benchmark.py "${THREAD_ID}"
else
  PYTHONPATH="${ROOT}/agents/src:${ROOT}/ingestion/src" \
    python3 "${SCRIPT}" "${THREAD_ID}" \
    --langgraph-url "${LANGGRAPH_URL:-http://127.0.0.1:8000}" \
    --ingestion-url "${INGESTION_API_URL:-http://127.0.0.1:8001}"
fi
