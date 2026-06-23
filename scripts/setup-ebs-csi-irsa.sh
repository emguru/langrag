#!/usr/bin/env bash
# Create IRSA for the EBS CSI driver and attach to the EKS addon.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SA_NAMESPACE="kube-system"
ROLE_NAME="AmazonEKS_EBS_CSI_DriverRole-${CLUSTER_NAME}"
MANAGED_POLICY_ARN="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
OIDC_ISSUER="$(aws eks describe-cluster \
  --name "${CLUSTER_NAME}" \
  --region "${AWS_REGION}" \
  --query 'cluster.identity.oidc.issuer' \
  --output text)"
OIDC_HOST="${OIDC_ISSUER#https://}"
OIDC_PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"

echo "==> Ensuring OIDC provider is registered..."
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${OIDC_PROVIDER_ARN}" &>/dev/null; then
  THUMBPRINT="$(openssl s_client -servername oidc.eks.${AWS_REGION}.amazonaws.com \
    -showcerts -connect "oidc.eks.${AWS_REGION}.amazonaws.com:443" </dev/null 2>/dev/null \
    | openssl x509 -fingerprint -noout -sha1 | cut -d= -f2 | tr -d ':')"
  aws iam create-open-id-connect-provider \
    --url "${OIDC_ISSUER}" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list "${THUMBPRINT}"
fi

TRUST_FILE="$(mktemp)"
cat > "${TRUST_FILE}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "${OIDC_PROVIDER_ARN}" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_HOST}:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "${OIDC_HOST}:sub": "system:serviceaccount:${SA_NAMESPACE}:ebs-csi-*"
        }
      }
    }
  ]
}
EOF

echo "==> Ensuring IAM role ${ROLE_NAME}..."
if ! aws iam get-role --role-name "${ROLE_NAME}" &>/dev/null; then
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "file://${TRUST_FILE}" >/dev/null
else
  aws iam update-assume-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-document "file://${TRUST_FILE}" >/dev/null
fi
rm -f "${TRUST_FILE}"

aws iam attach-role-policy --role-name "${ROLE_NAME}" --policy-arn "${MANAGED_POLICY_ARN}" 2>/dev/null || true
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo "    Role: ${ROLE_ARN}"

echo "==> Ensuring EBS CSI EKS addon with IRSA role..."
if aws eks describe-addon \
  --cluster-name "${CLUSTER_NAME}" \
  --addon-name aws-ebs-csi-driver \
  --region "${AWS_REGION}" &>/dev/null; then
  aws eks update-addon \
    --cluster-name "${CLUSTER_NAME}" \
    --addon-name aws-ebs-csi-driver \
    --service-account-role-arn "${ROLE_ARN}" \
    --resolve-conflicts OVERWRITE \
    --region "${AWS_REGION}"
else
  aws eks create-addon \
    --cluster-name "${CLUSTER_NAME}" \
    --addon-name aws-ebs-csi-driver \
    --service-account-role-arn "${ROLE_ARN}" \
    --resolve-conflicts OVERWRITE \
    --region "${AWS_REGION}"
fi

aws eks wait addon-active \
  --cluster-name "${CLUSTER_NAME}" \
  --addon-name aws-ebs-csi-driver \
  --region "${AWS_REGION}"

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" >/dev/null

for sa in ebs-csi-controller-sa ebs-csi-node-sa; do
  if kubectl get serviceaccount "${sa}" -n "${SA_NAMESPACE}" &>/dev/null; then
    kubectl annotate serviceaccount "${sa}" -n "${SA_NAMESPACE}" \
      eks.amazonaws.com/role-arn="${ROLE_ARN}" --overwrite
  fi
done

kubectl rollout restart deployment ebs-csi-controller -n kube-system 2>/dev/null || true
kubectl rollout restart daemonset ebs-csi-node -n kube-system 2>/dev/null || true

echo "==> EBS CSI IRSA ready."
