#!/usr/bin/env bash
# Print ACM DNS validation records for the showcase ingress certificate.
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

CERT_ARN="$(cdk_ingress_certificate_arn)"
APP_DOMAIN="$(cdk_app_domain_name)"

if [[ -z "$CERT_ARN" || "$CERT_ARN" == "None" ]]; then
  echo "No ingress certificate ARN in stack outputs yet."
  echo "Set appDomainName in cdk/cdk.json and run: ./scripts/infra-deploy.sh"
  exit 1
fi

echo "==> ACM certificate: ${CERT_ARN}"
echo "    Domain:          ${APP_DOMAIN}"
echo ""

STATUS="$(aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region "$AWS_REGION" \
  --query 'Certificate.Status' \
  --output text)"

echo "    Status:          ${STATUS}"
echo ""

if [[ "$STATUS" == "ISSUED" ]]; then
  echo "Certificate is ISSUED. Run: ./scripts/apply-showcase-access.sh"
  exit 0
fi

echo "Add this DNS record at your domain registrar (for ${APP_DOMAIN}):"
echo ""
aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region "$AWS_REGION" \
  --query 'Certificate.DomainValidationOptions[*].ResourceRecord' \
  --output table

echo ""
echo "After status is ISSUED:"
echo "  ./scripts/apply-showcase-access.sh"
echo "  Point ${APP_DOMAIN} CNAME → ALB hostname (from kubectl get ingress -n showcase)"
