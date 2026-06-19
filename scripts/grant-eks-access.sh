#!/usr/bin/env bash
# Grant the current AWS IAM identity admin access to the EKS cluster.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
AWS_REGION="${AWS_REGION:-us-east-1}"

PRINCIPAL="$(aws sts get-caller-identity --query Arn --output text)"
# Access entries require IAM ARN, not assumed-role session ARN with suffix
if [[ "$PRINCIPAL" == *":assumed-role/"* ]]; then
  echo "WARN: Using assumed-role ARN; use an IAM user or pass PRINCIPAL_ARN explicitly."
fi

echo "==> Granting EKS cluster admin to ${PRINCIPAL}"

MODE="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --region "${AWS_REGION}" \
  --query 'cluster.accessConfig.authenticationMode' --output text)"
if [[ "$MODE" == "CONFIG_MAP" ]]; then
  echo "==> Enabling API_AND_CONFIG_MAP authentication mode..."
  UPDATE_ID="$(aws eks update-cluster-config --name "${CLUSTER_NAME}" --region "${AWS_REGION}" \
    --access-config authenticationMode=API_AND_CONFIG_MAP \
    --query 'update.id' --output text)"
  aws eks wait cluster-active --name "${CLUSTER_NAME}" --region "${AWS_REGION}"
  for _ in $(seq 1 60); do
    S="$(aws eks describe-update --name "${CLUSTER_NAME}" --update-id "${UPDATE_ID}" \
      --query 'update.status' --output text 2>/dev/null || echo Pending)"
    [[ "$S" == "Successful" ]] && break
    sleep 10
  done
fi

aws eks create-access-entry \
  --cluster-name "${CLUSTER_NAME}" \
  --region "${AWS_REGION}" \
  --principal-arn "${PRINCIPAL}" \
  --type STANDARD 2>/dev/null || true

aws eks associate-access-policy \
  --cluster-name "${CLUSTER_NAME}" \
  --region "${AWS_REGION}" \
  --principal-arn "${PRINCIPAL}" \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}"
kubectl get nodes
echo "==> EKS access granted."
