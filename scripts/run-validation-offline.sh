#!/usr/bin/env bash
# Offline SWAG rerank validation — fixtures + in-memory index (no Qdrant/HTTP).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH="${ROOT}/ingestion/benchmarks"
FIXTURES_ROOT="${FIXTURES_ROOT:-${ROOT}/fixtures/corpus}"
SUITE="${SUITE:-all}"
SWAG_ROOT="${SWAG_ROOT:-$(cd "${ROOT}/../SWAG" 2>/dev/null && pwd || true)}"

PYTHON="${PYTHON:-python3}"
if [[ -n "${SWAG_ROOT}" && -x "${SWAG_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${SWAG_ROOT}/.venv/bin/python"
  if ! "${PYTHON}" -c "import ingestion" 2>/dev/null; then
    "${PYTHON}" -m pip install -e "${ROOT}/ingestion" -q
  fi
fi

PYTHONPATH="${BENCH}:${ROOT}/ingestion/src" \
  SWAG_PROFILE_MODE=heuristic \
  "${PYTHON}" "${BENCH}/validation_offline.py" \
  --fixtures-root "${FIXTURES_ROOT}" \
  --suite "${SUITE}"
