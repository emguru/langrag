#!/usr/bin/env bash
# Fast go/no-go validation. Offline by default (zero Bedrock cost).
#   ./scripts/validate-project.sh              # free — unit tests + SWAG (no LLM)
#   ./scripts/validate-project.sh --live-smoke  # +1 Titan embed (baseline search only)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/ingestion/.venv"
SCRIPT="${ROOT}/ingestion/benchmarks/go_no_go_report.py"

if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
  "${VENV}/bin/pip" install -q -e "${ROOT}/ingestion" pytest
fi

# Phase 1: always free offline (SWAG uses heuristic profiles — no Bedrock)
PYTHONPATH="${ROOT}/ingestion/src:${ROOT}/ingestion" \
  "${VENV}/bin/python" "${SCRIPT}"
OFFLINE_EXIT=$?

if [[ "${1:-}" != "--live-smoke" ]]; then
  exit "${OFFLINE_EXIT}"
fi

# Phase 2: minimal live smoke — baseline retrieval only (~1 Titan embed, no Sonnet)
if ! kubectl get deploy/ingestion -n rag >/dev/null 2>&1; then
  echo "WARN: no ingestion deployment — live smoke skipped" >&2
  exit "${OFFLINE_EXIT}"
fi

echo ""
echo "=== Live smoke (baseline only — 1 Titan embed, no Sonnet) ==="
kubectl exec -n rag deploy/ingestion -- python3 -c "
import httpx, json, sys
INGEST='http://127.0.0.1:8001'
Q='What is retrieval augmented generation?'
checks = {}

h = httpx.get(f'{INGEST}/health', timeout=15)
checks['ingestion_healthy'] = h.status_code == 200

stats = httpx.get(f'{INGEST}/rag/stats', params={'collection': 'research_docs'}, timeout=15).json()
points = stats.get('total_points') or 0
checks['corpus_non_empty'] = points > 0

base = httpx.post(f'{INGEST}/rag/search', json={'query': Q, 'collection': 'research_docs', 'top_k': 3, 'mode': 'baseline'}, timeout=120).json()
hits = base.get('hits') or []
checks['baseline_returns_hits'] = len(hits) > 0

report = {
    'corpus_points': points,
    'baseline_hits': len(hits),
    'top_score': hits[0].get('score') if hits else None,
    'bedrock_calls': '~1 Titan embed (no Sonnet — SWAG tested offline)',
    'checks': checks,
    'passed': all(checks.values()),
}
print(json.dumps(report, indent=2))
print()
print('Live smoke:', 'PASS' if report['passed'] else 'FAIL')
sys.exit(0 if report['passed'] else 1)
"
LIVE_EXIT=$?

if [[ "${OFFLINE_EXIT}" -eq 0 && "${LIVE_EXIT}" -eq 0 ]]; then
  echo ""
  echo "=== FINAL VERDICT: GO ==="
  exit 0
fi
echo ""
echo "=== FINAL VERDICT: NO-GO ==="
exit 1
