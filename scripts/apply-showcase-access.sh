#!/usr/bin/env bash
# Apply ClusterIP + optional Cognito ALB ingress, or fallback LoadBalancer service.
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

OUT_DIR="${ROOT}/.demo-deploy/showcase"
mkdir -p "$OUT_DIR"

kubectl apply -f "${OUT_DIR}/ui.yaml" 2>/dev/null || \
  kubectl apply -f "${ROOT}/k8s/showcase/ui.yaml"

INGRESS_ENABLED="$(cdk_ingress_enabled)"
CERT_ARN="$(cdk_ingress_certificate_arn)"
APP_DOMAIN="$(cdk_app_domain_name)"
POOL_ARN="$(cdk_cognito_user_pool_arn)"
CLIENT_ID="$(cdk_cognito_client_id)"
CLIENT_SECRET="$(cdk_cognito_client_secret)"
DOMAIN_PREFIX="$(cdk_cognito_domain_prefix)"

use_ingress=false
if [[ "$INGRESS_ENABLED" == "true" && -n "$CERT_ARN" && -n "$APP_DOMAIN" ]]; then
  CERT_STATUS="$(aws acm describe-certificate \
    --certificate-arn "$CERT_ARN" \
    --region "$AWS_REGION" \
    --query 'Certificate.Status' \
    --output text 2>/dev/null || echo "NOT_FOUND")"
  if [[ "$CERT_STATUS" == "ISSUED" ]]; then
    use_ingress=true
  else
    echo "WARN: ACM certificate not ISSUED yet (status=${CERT_STATUS})."
    echo "      Complete DNS validation, then re-run: ./scripts/apply-showcase-access.sh"
  fi
fi

CURRENT_MODE=""
if [[ -f "${ROOT}/.demo-access-mode" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.demo-access-mode"
  CURRENT_MODE="${ACCESS_MODE:-}"
fi

# Only replace ingress/LB when switching access modes or when forced.
# Deleting ingress on every apply creates a new ALB and breaks GoDaddy DNS.
if [[ "${FORCE_REPLACE_SHOWCASE_INGRESS:-}" == "true" ]]; then
  kubectl delete ingress showcase-ui -n showcase --ignore-not-found --wait=false
elif [[ "$use_ingress" == "true" && "$CURRENT_MODE" == "loadbalancer" ]]; then
  kubectl delete ingress showcase-ui -n showcase --ignore-not-found --wait=false
elif [[ "$use_ingress" != "true" && "$CURRENT_MODE" == "ingress" ]]; then
  kubectl delete ingress showcase-ui -n showcase --ignore-not-found --wait=false
fi

if [[ "$use_ingress" == "true" ]]; then
  if [[ -z "$CLIENT_SECRET" || "$CLIENT_SECRET" == "None" ]]; then
    echo "ERROR: Cognito client secret missing. Redeploy CDK with appDomainName set:"
    echo "  cd cdk && npm run build && npx cdk deploy"
    exit 1
  fi
  echo "==> Applying Cognito-protected ALB ingress (HTTPS)..."
  sed \
    -e "s|INGRESS_CERTIFICATE_ARN|${CERT_ARN}|g" \
    -e "s|COGNITO_USER_POOL_ARN|${POOL_ARN}|g" \
    -e "s|COGNITO_CLIENT_ID|${CLIENT_ID}|g" \
    -e "s|COGNITO_DOMAIN_PREFIX|${DOMAIN_PREFIX}|g" \
    -e "s|APP_DOMAIN_NAME|${APP_DOMAIN}|g" \
    "${ROOT}/k8s/showcase/ingress.yaml" > "${OUT_DIR}/ingress.yaml"
  python3 - "${OUT_DIR}/ingress.yaml" "${CLIENT_SECRET}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
secret = sys.argv[2]
text = path.read_text()
needle = '"UserPoolClientSecret":"COGNITO_CLIENT_SECRET"'
if needle not in text:
    raise SystemExit("ingress template missing UserPoolClientSecret placeholder")
path.write_text(
    text.replace(needle, f'"UserPoolClientSecret":{json.dumps(secret)}', 1)
)
PY
  if [[ "$CURRENT_MODE" == "loadbalancer" ]]; then
    kubectl delete svc showcase-ui -n showcase --ignore-not-found 2>/dev/null || true
  fi
  kubectl apply -f "${ROOT}/k8s/showcase/service.yaml"
  kubectl apply -f "${OUT_DIR}/ingress.yaml"
  cp "${OUT_DIR}/ingress.yaml" "${ROOT}/.demo-ingress.yaml"
  echo "ACCESS_MODE=ingress" > "${ROOT}/.demo-access-mode"
  ALB_HOST="$(kubectl get ingress showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo pending)"
  echo "APP_URL=https://${APP_DOMAIN}"
  echo "ALB_HOSTNAME=${ALB_HOST}"
  echo "    (Ingress updated in-place — ALB DNS stays stable across redeploys."
  echo "     Set GoDaddy CNAME langrag -> ALB_HOSTNAME once; only changes after demo-stop.)"
else
  if [[ -n "$APP_DOMAIN" && "$APP_DOMAIN" != "None" ]]; then
    if [[ "${ALLOW_INSECURE_DEMO:-}" != "true" ]]; then
      echo "ERROR: appDomainName is set (${APP_DOMAIN}) but HTTPS ingress is unavailable."
      echo "  Complete ACM DNS validation, then re-run this script."
      echo "  To override with a public HTTP NLB (no Cognito), set ALLOW_INSECURE_DEMO=true"
      exit 1
    fi
    echo "WARN: ALLOW_INSECURE_DEMO=true — deploying public HTTP NLB without Cognito."
  fi
  echo "==> Applying public LoadBalancer (no Cognito at edge — add appDomainName to cdk.json for HTTPS auth)..."
  kubectl delete svc showcase-ui -n showcase --ignore-not-found 2>/dev/null || true
  kubectl apply -f "${ROOT}/k8s/showcase/service-lb.yaml"
  cp "${ROOT}/k8s/showcase/service-lb.yaml" "${ROOT}/.demo-ui-svc.yaml"
  rm -f "${ROOT}/.demo-ingress.yaml"
  echo "ACCESS_MODE=loadbalancer" > "${ROOT}/.demo-access-mode"
  LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "pending")"
  echo "APP_URL=http://${LB}"
fi
