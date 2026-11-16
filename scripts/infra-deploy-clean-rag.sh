#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/cdk"

# Optional: export LANGSMITH_API_KEY and LANGSMITH_PROJECT before deploy for traces

npm install
npm run build
npx cdk bootstrap
npx cdk deploy LangragCleanRag --require-approval never

echo ""
echo "Deployed. Fetch outputs:"
npx cdk deploy LangragCleanRag --require-approval never --outputs-file "$ROOT/cdk-outputs.json"
cat "$ROOT/cdk-outputs.json"
