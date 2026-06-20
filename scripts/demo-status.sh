#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

echo "=== Demo Status ==="
echo ""

STACK_STATUS="$(aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME:-KubeRagLanggDemo}" \
  --query 'Stacks[0].StackStatus' \
  --output text 2>/dev/null || echo "NOT_FOUND")"

if [[ "$STACK_STATUS" == "CREATE_COMPLETE" || "$STACK_STATUS" == "UPDATE_COMPLETE" ]]; then
  echo "Infrastructure: PROVISIONED (CDK)"
  echo "  Stack:        ${STACK_NAME:-KubeRagLanggDemo} (${STACK_STATUS})"
  echo "  Cluster:      $(cdk_cluster_name)"
  echo "  Demo mode:    $(cdk_demo_mode)"
  echo "  S3 bucket:    $(cdk_documents_bucket)"
  RDS_ID="$(cdk_rds_instance_id)"
  if [[ -n "$RDS_ID" && "$RDS_ID" != "None" && "$RDS_ID" != "" ]]; then
    RDS_STATUS="$(aws rds describe-db-instances \
      --db-instance-identifier "$RDS_ID" \
      --query 'DBInstances[0].DBInstanceStatus' \
      --output text 2>/dev/null || echo "unknown")"
    echo "  RDS:          ${RDS_ID} (${RDS_STATUS})"
  else
    echo "  RDS:          disabled (demo default)"
  fi
else
  echo "Infrastructure: ${STACK_STATUS}"
  echo "  Deploy with:  ./scripts/infra-deploy.sh"
fi

echo ""

if kubectl cluster-info &>/dev/null 2>&1; then
  NODES="$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  READY_NODES="$(kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready ' || echo 0)"
  echo "Kubernetes: CONNECTED"
  echo "  Nodes:        ${READY_NODES}/${NODES} ready"

  for ns in rag agents showcase; do
    if kubectl get namespace "$ns" &>/dev/null; then
      RUNNING="$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | grep -c 'Running' || echo 0)"
      TOTAL="$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
      echo "  ${ns}:$(printf '%*s' $((12 - ${#ns})) '') ${RUNNING}/${TOTAL} pods running"
    fi
  done

  POOL_ID="$(cdk_cognito_user_pool_id 2>/dev/null || true)"
  [[ -n "$POOL_ID" && "$POOL_ID" != "None" ]] && echo "  Cognito pool: ${POOL_ID}"
  if [[ -f "${ROOT}/.demo-access-mode" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT}/.demo-access-mode"
    if [[ "${ACCESS_MODE:-}" == "ingress" ]]; then
      echo "  UI URL:       https://$(cdk_app_domain_name) (Cognito)"
    else
      LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")"
      [[ -n "$LB" ]] && echo "  UI URL:       http://${LB}" || echo "  UI URL:       (stopped — no LoadBalancer)"
    fi
  else
    LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")"
    [[ -n "$LB" ]] && echo "  UI URL:       http://${LB}" || echo "  UI URL:       (stopped — no LoadBalancer)"
  fi
else
  echo "Kubernetes: NOT CONNECTED"
fi

[[ -f "${ROOT}/.demo-state" ]] && echo "" && echo "Last stop: $(grep STOPPED_AT "${ROOT}/.demo-state" | cut -d= -f2)"

echo ""
echo "=== Cost estimate ==="
echo "  Running 24/7:  ~\$185/mo  |  Stopped: ~\$105/mo  |  Parked: ~\$0-1/mo"
echo ""
echo "  ./scripts/demo-stop.sh  |  ./scripts/demo-start.sh  |  ./scripts/demo-park.sh"
