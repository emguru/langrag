#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
DEMO_NODE_DESIRED="${DEMO_NODE_DESIRED:-1}"

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

echo "==> Starting demo environment..."

NODEGROUP="$(cdk_node_group_name)"
MAX_SIZE="$(cdk_node_max_size)"
MAX_SIZE="${MAX_SIZE:-2}"

RDS_ID="$(cdk_rds_instance_id)"
if [[ -n "$RDS_ID" && "$RDS_ID" != "None" && "$RDS_ID" != "" ]]; then
  STATUS="$(aws rds describe-db-instances \
    --db-instance-identifier "$RDS_ID" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text 2>/dev/null || echo "unknown")"
  if [[ "$STATUS" == "stopped" ]]; then
    echo "    Starting RDS ${RDS_ID}..."
    aws rds start-db-instance --db-instance-identifier "$RDS_ID"
    aws rds wait db-instance-available --db-instance-identifier "$RDS_ID"
  fi
fi

if [[ -n "$NODEGROUP" && "$NODEGROUP" != "None" ]]; then
  echo "    Scaling node group ${NODEGROUP} → desired=${DEMO_NODE_DESIRED}..."
  aws eks update-nodegroup-config \
    --region "${AWS_REGION}" \
    --cluster-name "${CLUSTER_NAME}" \
    --nodegroup-name "${NODEGROUP}" \
    --scaling-config "minSize=0,maxSize=${MAX_SIZE},desiredSize=${DEMO_NODE_DESIRED}"

  echo "    Waiting for nodes..."
  kubectl wait --for=condition=Ready nodes --all --timeout=600s 2>/dev/null || {
    sleep 60
    kubectl wait --for=condition=Ready nodes --all --timeout=300s
  }
fi

kubectl scale deploy qdrant redis ingestion -n rag --replicas=1 2>/dev/null || true
kubectl scale deploy research-agents -n agents --replicas=1 2>/dev/null || true
kubectl scale deploy showcase-ui -n showcase --replicas=1 2>/dev/null || true

if ! kubectl get svc showcase-ui -n showcase &>/dev/null && \
   ! kubectl get ingress showcase-ui -n showcase &>/dev/null; then
  echo "    Recreating showcase access (LoadBalancer or Cognito ingress)..."
  if [[ -f "${ROOT}/.demo-deploy/showcase/ui.yaml" ]]; then
    "${ROOT}/scripts/apply-showcase-access.sh"
  elif [[ -f "${ROOT}/.demo-ui-svc.yaml" ]]; then
    kubectl apply -f "${ROOT}/.demo-ui-svc.yaml"
  else
    UI_MANIFEST="$(find "${ROOT}/.demo-deploy" -name "ui.yaml" 2>/dev/null | head -1)"
    [[ -n "$UI_MANIFEST" ]] && kubectl apply -f "$UI_MANIFEST" || \
      echo "    WARN: Run ./scripts/deploy.sh first"
  fi
fi

kubectl wait --for=condition=Available deployment --all -n rag --timeout=300s 2>/dev/null || true
kubectl wait --for=condition=Available deployment --all -n agents --timeout=300s 2>/dev/null || true
kubectl wait --for=condition=Available deployment --all -n showcase --timeout=300s 2>/dev/null || true

echo ""
echo "==> Demo started!"
if [[ -f "${ROOT}/.demo-access-mode" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.demo-access-mode"
  if [[ "${ACCESS_MODE:-}" == "ingress" ]]; then
    APP_DOMAIN="$(cdk_app_domain_name)"
    echo "    UI URL:       https://${APP_DOMAIN}  (Cognito)"
  else
    LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "(pending)")"
    echo "    UI URL:       http://${LB}"
  fi
else
  LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "(pending)")"
  ING="$(kubectl get ingress showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")"
  [[ -n "$ING" ]] && echo "    UI URL:       https://${ING}" || echo "    UI URL:       http://${LB}"
fi
