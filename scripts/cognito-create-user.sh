#!/usr/bin/env bash
# Create a Cognito user in the showcase user pool (admin invite + permanent password).
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

POOL_ID="$(cdk_cognito_user_pool_id)"
if [[ -z "$POOL_ID" || "$POOL_ID" == "None" ]]; then
  echo "ERROR: Cognito user pool not found. Run ./scripts/infra-deploy.sh first."
  exit 1
fi

EMAIL="${1:-}"
PASSWORD="${2:-}"

if [[ -z "$EMAIL" ]]; then
  read -r -p "Email (username): " EMAIL
fi
if [[ -z "$PASSWORD" ]]; then
  read -r -s -p "Password (min 12 chars, upper+lower+digit): " PASSWORD
  echo ""
fi

echo "==> Creating Cognito user in pool ${POOL_ID}..."

if aws cognito-idp admin-get-user \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --region "$AWS_REGION" &>/dev/null; then
  echo "    User ${EMAIL} already exists — setting password only."
else
  aws cognito-idp admin-create-user \
    --user-pool-id "$POOL_ID" \
    --username "$EMAIL" \
    --user-attributes "Name=email,Value=${EMAIL}" "Name=email_verified,Value=true" \
    --message-action SUPPRESS \
    --region "$AWS_REGION"
  echo "    Created user ${EMAIL}"
fi

aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --password "$PASSWORD" \
  --permanent \
  --region "$AWS_REGION"

HOSTED="$(cdk_cognito_hosted_ui_url)"
echo ""
echo "==> User ready."
echo "    Pool ID:    ${POOL_ID}"
echo "    Hosted UI:  ${HOSTED}"
echo ""
echo "    With ALB+Cognito ingress (after appDomainName + ACM cert):"
echo "      https://$(cdk_app_domain_name)/"
echo ""
echo "    Without ingress yet: create users now; enable HTTPS ingress when you have a domain."
