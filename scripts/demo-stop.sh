#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
STATE_FILE="${ROOT}/.demo-state"

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

echo "==> Stopping demo environment..."

for ns in agents rag showcase monitoring; do
  if kubectl get namespace "$ns" &>/dev/null; then
    kubectl scale deploy --all -n "$ns" --replicas=0 2>/dev/null || true
    kubectl scale statefulset --all -n "$ns" --replicas=0 2>/dev/null || true
  fi
done

if kubectl get svc showcase-ui -n showcase &>/dev/null; then
  LB_HOSTNAME="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  kubectl annotate svc showcase-ui -n showcase \
    "demo.kube-rag-langg/last-lb=${LB_HOSTNAME}" --overwrite 2>/dev/null || true
  kubectl delete svc showcase-ui -n showcase --ignore-not-found
  echo "    Removed LoadBalancer service (saves ~\$16/mo while stopped)"
fi

if kubectl get ingress showcase-ui -n showcase &>/dev/null; then
  INGRESS_HOST="$(kubectl get ingress showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  kubectl annotate ingress showcase-ui -n showcase \
    "demo.kube-rag-langg/last-host=${INGRESS_HOST}" --overwrite 2>/dev/null || true
  kubectl delete ingress showcase-ui -n showcase --ignore-not-found
  echo "    Removed ALB ingress (Cognito-protected)"
fi

NODEGROUP="$(cdk_node_group_name)"
if [[ -n "$NODEGROUP" && "$NODEGROUP" != "None" ]]; then
  MAX_SIZE="$(cdk_node_max_size)"
  MAX_SIZE="${MAX_SIZE:-2}"
  aws eks update-nodegroup-config \
    --region "${AWS_REGION}" \
    --cluster-name "${CLUSTER_NAME}" \
    --nodegroup-name "${NODEGROUP}" \
    --scaling-config "minSize=0,maxSize=${MAX_SIZE},desiredSize=0"
  echo "    Node group ${NODEGROUP} → desired=0"
else
  echo "    WARN: Could not read node group name from CDK stack outputs"
fi

RDS_ID="$(cdk_rds_instance_id)"
if [[ -n "$RDS_ID" && "$RDS_ID" != "None" && "$RDS_ID" != "" ]]; then
  STATUS="$(aws rds describe-db-instances \
    --db-instance-identifier "$RDS_ID" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text 2>/dev/null || echo "unknown")"
  if [[ "$STATUS" == "available" ]]; then
    aws rds stop-db-instance --db-instance-identifier "$RDS_ID"
    echo "    RDS ${RDS_ID} stopping"
  fi
fi

cat > "${STATE_FILE}" <<EOF
STOPPED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
CLUSTER_NAME=${CLUSTER_NAME}
AWS_REGION=${AWS_REGION}
NODEGROUP=${NODEGROUP}
RDS_ID=${RDS_ID}
EOF

echo ""
echo "==> Demo stopped."
echo "    Restart with: ./scripts/demo-start.sh"
echo "    Full teardown: ./scripts/demo-park.sh"
