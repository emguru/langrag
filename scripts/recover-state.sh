#!/usr/bin/env bash
# Recover from a failed/partial CDK deploy or race with manual AWS console changes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${CDK_STACK_NAME:-KubeRagLanggDemo}"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
AWS_REGION="${AWS_REGION:-us-east-1}"

echo "=== Recovery check ==="
echo ""

# Stop any stale local deploys that could race CloudFormation
pkill -f "cdk deploy.*KubeRagLanggDemo" 2>/dev/null || true
pkill -f "infra-deploy.sh" 2>/dev/null || true
pkill -f "cloudformation wait stack-create-complete --stack-name ${STACK_NAME}" 2>/dev/null || true
echo "Stopped stale local deploy processes (if any)."
echo ""

STACK_STATUS="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --query 'Stacks[0].StackStatus' \
  --output text 2>/dev/null || echo "NOT_FOUND")"
echo "CloudFormation stack: ${STACK_STATUS}"

if [[ "$STACK_STATUS" == "ROLLBACK_IN_PROGRESS" || "$STACK_STATUS" == "CREATE_IN_PROGRESS" ]]; then
  echo "Waiting for stack to settle (do not use AWS console to modify resources now)..."
  if [[ "$STACK_STATUS" == "ROLLBACK_IN_PROGRESS" ]]; then
    aws cloudformation wait stack-rollback-complete --stack-name "${STACK_NAME}"
    STACK_STATUS="ROLLBACK_COMPLETE"
  fi
fi

if [[ "$STACK_STATUS" == "ROLLBACK_COMPLETE" || "$STACK_STATUS" == "CREATE_FAILED" ]]; then
  echo "Deleting failed stack ${STACK_NAME}..."
  aws cloudformation delete-stack --stack-name "${STACK_NAME}"
  aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" || true
  STACK_STATUS="NOT_FOUND"
fi

CLUSTER_STATUS="$(aws eks describe-cluster --name "${CLUSTER_NAME}" \
  --query 'cluster.status' --output text 2>/dev/null || echo "NOT_FOUND")"
if [[ "$CLUSTER_STATUS" != "NOT_FOUND" ]]; then
  CLUSTER_VERSION="$(aws eks describe-cluster --name "${CLUSTER_NAME}" \
    --query 'cluster.version' --output text 2>/dev/null || echo "?")"
  echo "Orphan EKS cluster: ${CLUSTER_NAME} (${CLUSTER_STATUS}, k8s ${CLUSTER_VERSION})"

  # Delete nodegroups first
  for ng in $(aws eks list-nodegroups --cluster-name "${CLUSTER_NAME}" \
    --query 'nodegroups[]' --output text 2>/dev/null); do
    echo "  Deleting nodegroup ${ng}..."
    aws eks delete-nodegroup --cluster-name "${CLUSTER_NAME}" --nodegroup-name "${ng}" 2>/dev/null || true
    aws eks wait nodegroup-deleted --cluster-name "${CLUSTER_NAME}" --nodegroup-name "${ng}" 2>/dev/null || true
  done

  echo "  Deleting cluster ${CLUSTER_NAME}..."
  aws eks delete-cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
  aws eks wait cluster-deleted --name "${CLUSTER_NAME}" 2>/dev/null || true
fi

echo ""
echo "=== Recovery complete ==="
echo "Safe to redeploy:"
echo "  ./scripts/infra-deploy.sh"
echo "  ./scripts/bootstrap.sh"
echo "  ./scripts/deploy.sh"
echo ""
echo "Tip: avoid manual EKS changes in the AWS console while CDK deploy is running."
