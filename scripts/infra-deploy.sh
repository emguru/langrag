#!/usr/bin/env bash
# Deploy AWS infrastructure via CDK.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env for secret values passed into CDK
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

export AWS_REGION="${AWS_REGION:-us-east-1}"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
export CDK_DEFAULT_REGION="${AWS_REGION}"

echo "==> CDK deploy (account=${CDK_DEFAULT_ACCOUNT}, region=${AWS_REGION})"

cd "${ROOT}/cdk"

if [[ ! -d node_modules ]]; then
  echo "==> Installing CDK dependencies..."
  npm install
fi

echo "==> Bootstrapping CDK (if needed)..."
npx cdk bootstrap "aws://${CDK_DEFAULT_ACCOUNT}/${AWS_REGION}" 2>/dev/null || true

echo "==> Synthesizing..."
npm run build
npx cdk synth

echo "==> Deploying stack KubeRagLanggDemo (~15-20 min for EKS)..."
npx cdk deploy --require-approval never

echo ""
echo "==> Infrastructure deployed."
# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"
echo "    Cluster:  $(cdk_cluster_name)"
echo "    S3:       $(cdk_documents_bucket)"
echo ""
echo "    Next steps:"
echo "      $(cdk_kubeconfig_cmd)"
echo "      ./scripts/bootstrap.sh"
echo "      ./scripts/deploy.sh"
