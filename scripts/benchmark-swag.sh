#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/ingestion/.venv-bench"

if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
  "${VENV}/bin/pip" install -q -e "${ROOT}/ingestion"
fi

PYTHONPATH="${ROOT}/ingestion/src:${ROOT}/ingestion" \
  "${VENV}/bin/python" "${ROOT}/ingestion/benchmarks/swag_integrity_benchmark.py"
