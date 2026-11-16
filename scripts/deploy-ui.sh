#!/usr/bin/env bash
# Build the React UI and deploy to S3 + CloudFront (LangragUi stack).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_ENDPOINT="${API_ENDPOINT:-}"

echo "Building UI..."
cd "$ROOT/ui"
POOL_ID="${COGNITO_USER_POOL_ID:-}"
CLIENT_ID="${COGNITO_CLIENT_ID:-}"
AUTH_REGION="${COGNITO_REGION:-${AWS_REGION:-us-east-1}}"
if [[ -z "$POOL_ID" || -z "$CLIENT_ID" ]]; then
  POOL_ID="$(aws cloudformation describe-stacks --stack-name LangragCleanRag \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text 2>/dev/null || true)"
  CLIENT_ID="$(aws cloudformation describe-stacks --stack-name LangragCleanRag \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text 2>/dev/null || true)"
  AUTH_REGION="$(aws cloudformation describe-stacks --stack-name LangragCleanRag \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolRegion'].OutputValue" --output text 2>/dev/null || true)"
fi
if [[ -n "$POOL_ID" && "$POOL_ID" != "None" && -n "$CLIENT_ID" && "$CLIENT_ID" != "None" ]]; then
  python3 - <<PY
import json
from pathlib import Path
Path("public/auth-config.json").write_text(json.dumps({
    "userPoolId": "$POOL_ID",
    "clientId": "$CLIENT_ID",
    "region": "${AUTH_REGION:-us-east-1}",
}, indent=2) + "\n")
print("Wrote public/auth-config.json")
PY
fi
npm install
npm run build
cat > public/auth-config.json <<'EOF'
{
  "userPoolId": "",
  "clientId": "",
  "region": "us-east-1"
}
EOF

echo "Deploying UI stack..."
cd "$ROOT/cdk"
npm run build

DEPLOY_ARGS=(
  LangragUi
  --require-approval never
  --outputs-file "$ROOT/cdk-ui-outputs.json"
  -c "domainName=${DOMAIN_NAME:-rag.automatico.llc}"
  -c "hostedZoneDomain=${HOSTED_ZONE_DOMAIN:-automatico.llc}"
  -c "hostedZoneId=${HOSTED_ZONE_ID:-Z03675412E5MU377HS9OE}"
)
if [[ -n "$API_ENDPOINT" ]]; then
  DEPLOY_ARGS=(-c "apiEndpoint=$API_ENDPOINT" "${DEPLOY_ARGS[@]}")
fi

npx cdk deploy "${DEPLOY_ARGS[@]}"

echo ""
echo "Frontend deployed:"
cat "$ROOT/cdk-ui-outputs.json"
