#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
AWS_REGION="${AWS_REGION:-us-east-1}"

echo "==> Bootstrapping EKS cluster add-ons for ${CLUSTER_NAME}..."

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}"

echo "==> Installing EBS CSI driver addon..."
if ! aws eks describe-addon \
  --cluster-name "${CLUSTER_NAME}" \
  --addon-name aws-ebs-csi-driver \
  --region "${AWS_REGION}" &>/dev/null; then
  aws eks create-addon \
    --cluster-name "${CLUSTER_NAME}" \
    --addon-name aws-ebs-csi-driver \
    --resolve-conflicts OVERWRITE \
    --region "${AWS_REGION}"
fi
aws eks wait addon-active \
  --cluster-name "${CLUSTER_NAME}" \
  --addon-name aws-ebs-csi-driver \
  --region "${AWS_REGION}" \
  --max-attempts 30 2>/dev/null || echo "WARN: EBS CSI addon not active yet - continuing"

if ! command -v helm &>/dev/null; then
  echo "WARN: helm not found - skipping ALB controller, external-secrets, autoscaler."
  echo "      Install helm via: brew install helm"
else
  helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
  helm repo update

  helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
    -n kube-system \
    --set clusterName="${CLUSTER_NAME}" \
    --set serviceAccount.create=true \
    --set serviceAccount.name=aws-load-balancer-controller \
    --wait || echo "WARN: ALB controller install failed - ensure IAM policy is attached"

  echo "==> Configuring ALB controller IRSA..."
  "${ROOT}/scripts/setup-alb-irsa.sh"

  echo "==> Installing External Secrets Operator..."
  helm repo add external-secrets https://charts.external-secrets.io 2>/dev/null || true
  helm upgrade --install external-secrets external-secrets/external-secrets \
    -n external-secrets --create-namespace --wait

  echo "==> Installing cluster autoscaler..."
  helm repo add autoscaler https://kubernetes.github.io/autoscaler 2>/dev/null || true
  helm upgrade --install cluster-autoscaler autoscaler/cluster-autoscaler \
    -n kube-system \
    --set autoDiscovery.clusterName="${CLUSTER_NAME}" \
    --set awsRegion="${AWS_REGION}" \
    --wait || echo "WARN: cluster-autoscaler install failed - ensure IAM role exists"
fi

echo "==> Applying base Kubernetes resources..."
kubectl apply -f "${ROOT}/k8s/base/namespaces.yaml"
kubectl apply -f "${ROOT}/k8s/base/storageclass.yaml"
kubectl apply -f "${ROOT}/k8s/base/configmap.yaml"

echo "==> Bootstrap complete."
