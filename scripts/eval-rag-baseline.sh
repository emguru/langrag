#!/usr/bin/env bash
# Retrieval smoke test against the live hybrid search path.
set -euo pipefail
BASE="${1:-http://localhost:8001}"

if curl -sf -X POST "$BASE/rag/eval" -H 'Content-Type: application/json' -o /tmp/langrag-eval.json; then
  python3 - <<'PY'
import json, sys
data = json.load(open("/tmp/langrag-eval.json"))
print(f"{data.get('passed')} / {data.get('total')} queries returned chunks")
for row in data.get("results") or []:
    status = "PASS" if row.get("hit_count", 0) > 0 else "FAIL"
    print(f"  {status} {row.get('query')} ({row.get('hit_count')} hits)")
sys.exit(0 if data.get("ok") else 1)
PY
  exit $?
fi

QUERIES=(
  "what is retrieval augmented generation"
  "how does chunking work"
  "langgraph human in the loop"
)

pass=0
total=${#QUERIES[@]}

for q in "${QUERIES[@]}"; do
  echo "Query: $q"
  hits=$(curl -sf -X POST "$BASE/rag/search" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"$q\",\"top_k\":3}" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('hits',[])))")
  if [[ "$hits" -gt 0 ]]; then
    echo "  PASS ($hits hits)"
    pass=$((pass + 1))
  else
    echo "  FAIL (0 hits)"
  fi
done

echo ""
echo "$pass / $total queries returned chunks"
[[ "$pass" -ge 1 ]]
