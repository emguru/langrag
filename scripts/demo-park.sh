#!/usr/bin/env bash
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PURGE_DATA=false

for arg in "$@"; do
  case "$arg" in
    --purge-data) PURGE_DATA=true ;;
    -h|--help)
      echo "Usage: demo-park.sh [--purge-data]"
      exit 0
      ;;
  esac
done

echo "==> Parking demo (CDK destroy)..."
echo "    Removes EKS, VPC, NAT. S3 + ECR retained by default."
echo ""
read -r -p "Continue? [y/N] " confirm
[[ "${DEMO_PARK_CONFIRM:-}" == "yes" ]] && confirm="y"
confirm_lc="$(printf '%s' "$confirm" | tr '[:upper:]' '[:lower:]')"
[[ "$confirm_lc" != "y" && "$confirm_lc" != "yes" ]] && { echo "Aborted."; exit 0; }

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

if kubectl cluster-info &>/dev/null; then
  "${ROOT}/scripts/demo-stop.sh" 2>/dev/null || true
fi

if [[ "$PURGE_DATA" == "true" ]]; then
  BUCKET="$(cdk_documents_bucket)"
  if [[ -n "$BUCKET" && "$BUCKET" != "None" ]]; then
    echo "==> Purging S3 documents..."
    aws s3 rm "s3://${BUCKET}" --recursive 2>/dev/null || true
  fi
fi

cd "${ROOT}/cdk"
npm run destroy

rm -f "${ROOT}/.demo-state" "${ROOT}/.demo-ui-svc.yaml"
rm -rf "${ROOT}/.demo-deploy"

echo ""
echo "==> Demo parked. Idle cost: ~\$0-1/mo (S3 storage)."
echo "    Restart: ./scripts/infra-deploy.sh && ./scripts/bootstrap.sh && ./scripts/deploy.sh"
