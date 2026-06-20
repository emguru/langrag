#!/usr/bin/env bash
# Enable HTTPS + Cognito at the ALB (requires a domain you control).
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
CDK_JSON="${ROOT}/cdk/cdk.json"

DOMAIN="${1:-${APP_DOMAIN_NAME:-}}"
if [[ -z "$DOMAIN" ]]; then
  echo "Usage: $0 <app-domain-name>"
  echo "  e.g. $0 research.demo.example.com"
  echo "  or:  APP_DOMAIN_NAME=research.demo.example.com $0"
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "ERROR: jq is required. Install with: brew install jq"
  exit 1
fi

CURRENT="$(jq -r '.context.appDomainName // ""' "$CDK_JSON")"
if [[ "$CURRENT" == "$DOMAIN" ]]; then
  echo "==> appDomainName already set to ${DOMAIN}"
else
  echo "==> Setting appDomainName=${DOMAIN} in cdk/cdk.json"
  tmp="$(mktemp)"
  jq --arg d "$DOMAIN" '.context.appDomainName = $d' "$CDK_JSON" > "$tmp"
  mv "$tmp" "$CDK_JSON"
fi

export AWS_REGION
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
export CDK_DEFAULT_REGION="${AWS_REGION}"

echo "==> Deploying CDK stack (ACM cert + Cognito OAuth callbacks)..."
cd "${ROOT}/cdk"
npm run build
npx cdk deploy --require-approval never

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"
CERT_ARN="$(cdk_ingress_certificate_arn)"
echo ""
echo "==> Next steps:"
echo "  1. ACM DNS validation — add CNAME(s) for certificate:"
echo "       ${CERT_ARN}"
echo "     aws acm describe-certificate --certificate-arn ${CERT_ARN} --region ${AWS_REGION}"
echo "  2. Wait until status is ISSUED"
echo "  3. Apply ingress: ./scripts/apply-showcase-access.sh"
echo "  4. Point ${DOMAIN} CNAME → ALB hostname (printed by apply-showcase-access or kubectl get ingress)"
echo "  5. Create users: ./scripts/cognito-create-user.sh you@company.com"
echo ""
echo "  See docs/SECURITY.md and docs/COGNITO.md"
