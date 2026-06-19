#!/usr/bin/env bash
# Create IRSA for AWS Load Balancer Controller and annotate its service account.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SA_NAMESPACE="kube-system"
SA_NAME="aws-load-balancer-controller"
POLICY_NAME="AWSLoadBalancerControllerIAMPolicy-${CLUSTER_NAME}"
ROLE_NAME="AmazonEKSLoadBalancerControllerRole-${CLUSTER_NAME}"
POLICY_FILE="${ROOT}/cdk/policies/alb-controller-iam-policy.json"

if [[ ! -f "${POLICY_FILE}" ]]; then
  echo "ERROR: missing ${POLICY_FILE}"
  exit 1
fi

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

echo "==> Ensuring IAM policy ${POLICY_NAME}..."
POLICY_ARN="$(aws iam list-policies --scope Local --query "Policies[?PolicyName=='${POLICY_NAME}'].Arn | [0]" --output text)"
if [[ -z "${POLICY_ARN}" || "${POLICY_ARN}" == "None" ]]; then
  POLICY_ARN="$(aws iam create-policy \
    --policy-name "${POLICY_NAME}" \
    --policy-document "file://${POLICY_FILE}" \
    --query 'Policy.Arn' --output text)"
fi
echo "    Policy: ${POLICY_ARN}"

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
          "${OIDC_HOST}:aud": "sts.amazonaws.com",
          "${OIDC_HOST}:sub": "system:serviceaccount:${SA_NAMESPACE}:${SA_NAME}"
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

aws iam attach-role-policy --role-name "${ROLE_NAME}" --policy-arn "${POLICY_ARN}" 2>/dev/null || true
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo "    Role:   ${ROLE_ARN}"

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" >/dev/null

echo "==> Annotating service account ${SA_NAMESPACE}/${SA_NAME}..."
kubectl create serviceaccount "${SA_NAME}" -n "${SA_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl annotate serviceaccount "${SA_NAME}" -n "${SA_NAMESPACE}" \
  eks.amazonaws.com/role-arn="${ROLE_ARN}" --overwrite

if kubectl get deployment aws-load-balancer-controller -n "${SA_NAMESPACE}" &>/dev/null; then
  echo "==> Restarting AWS Load Balancer Controller..."
  kubectl rollout restart deployment aws-load-balancer-controller -n "${SA_NAMESPACE}"
  kubectl rollout status deployment aws-load-balancer-controller -n "${SA_NAMESPACE}" --timeout=120s
fi

echo "==> ALB controller IRSA ready."
