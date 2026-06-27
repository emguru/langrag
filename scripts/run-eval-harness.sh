#!/usr/bin/env bash
# SWAG Evaluation Harness — offline multi-corpus baseline comparison.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="${ROOT}/ingestion/benchmarks/eval_harness"
FIXTURES_ROOT="${FIXTURES_ROOT:-${ROOT}/fixtures/corpus}"
PYTHON="${PYTHON:-python3}"

if [[ -x "${ROOT}/ingestion/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/ingestion/.venv/bin/python"
fi

export PYTHONPATH="${ROOT}/ingestion/benchmarks:${ROOT}/ingestion/src:${HARNESS}"
export SWAG_PROFILE_MODE="${SWAG_PROFILE_MODE:-heuristic}"
export SWAG_GATED_RERANK="${SWAG_GATED_RERANK:-false}"

# Optional: ./scripts/run-eval-harness.sh --generate

mkdir -p "${HARNESS}/results"

exec "${PYTHON}" "${HARNESS}/run.py" \
  --fixtures-root "${FIXTURES_ROOT}" \
  "$@"
