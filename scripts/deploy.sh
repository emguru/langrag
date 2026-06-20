#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="${CLUSTER_NAME:-kube-rag-langg}"
DEPLOYMENT_TIER="${DEPLOYMENT_TIER:-cloud}"
DEMO_MODE="${DEMO_MODE:-true}"
ENABLE_MONITORING="${ENABLE_MONITORING:-false}"

# Load .env if present
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

# shellcheck disable=SC1091
source "${ROOT}/scripts/cdk-outputs.sh"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_BASE="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${CLUSTER_NAME}"

echo "==> Demo deploy (DEMO_MODE=${DEMO_MODE}, monitoring=${ENABLE_MONITORING})"

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" 2>/dev/null || true

echo "==> Logging in to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Building and pushing images (linux/amd64)..."
if docker buildx version &>/dev/null; then
  docker buildx create --name kube-amd64 --driver docker-container --use 2>/dev/null || docker buildx use kube-amd64
  docker buildx inspect --bootstrap &>/dev/null || true
  for svc in agents ingestion ui; do
    tag="${ECR_BASE}/${svc}:latest"
    echo "  Building ${svc}..."
    docker buildx build --platform linux/amd64 --push -t "${tag}" "${ROOT}/${svc}"
  done
else
  for svc in agents ingestion ui; do
    tag="${ECR_BASE}/${svc}:latest"
    echo "  Building ${svc}..."
    docker build --platform linux/amd64 -t "${tag}" "${ROOT}/${svc}"
    docker push "${tag}"
  done
fi

echo "==> Fetching CDK stack outputs..."
IRSA_ARN="$(cdk_agents_irsa_arn)"
INGESTION_IRSA_ARN="$(cdk_ingestion_irsa_arn)"
S3_BUCKET="$(cdk_documents_bucket)"

echo "==> Syncing secrets..."
COMMON_SECRET_ARGS=(
  --from-literal=LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-}"
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}"
  --from-literal=S3_DOCUMENT_BUCKET="${S3_BUCKET}"
  --from-literal=AWS_REGION="${AWS_REGION}"
  --from-literal=LLM_PROVIDER=bedrock
)
kubectl create secret generic app-secrets \
  "${COMMON_SECRET_ARGS[@]}" \
  --from-literal=TAVILY_API_KEY="${TAVILY_API_KEY:-}" \
  -n agents --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic app-secrets \
  "${COMMON_SECRET_ARGS[@]}" \
  -n rag --dry-run=client -o yaml | kubectl apply -f -
# showcase-ui uses configMap only — no LLM or LangSmith keys in the UI pod

mkdir -p "${ROOT}/.demo-deploy/rag" "${ROOT}/.demo-deploy/agents" "${ROOT}/.demo-deploy/showcase"

echo "==> Deploying RAG stack (Qdrant + in-cluster Redis + ingestion)..."
sed \
  -e "s|INGESTION_IMAGE|${ECR_BASE}/ingestion:latest|g" \
  -e "s|INGESTION_IRSA_ROLE_ARN|${INGESTION_IRSA_ARN}|g" \
  "${ROOT}/k8s/rag/ingestion.yaml" > "${ROOT}/.demo-deploy/rag/ingestion.yaml"
cp "${ROOT}/k8s/rag/qdrant.yaml" "${ROOT}/.demo-deploy/rag/"
cp "${ROOT}/k8s/rag/redis.yaml" "${ROOT}/.demo-deploy/rag/"
kubectl apply -f "${ROOT}/k8s/base/namespaces.yaml"
kubectl apply -f "${ROOT}/k8s/base/storageclass.yaml"
kubectl apply -f "${ROOT}/k8s/base/configmap.yaml"
kubectl apply -f "${ROOT}/k8s/base/network-policies.yaml"
kubectl apply -f "${ROOT}/.demo-deploy/rag/"

echo "==> Deploying research agents..."
sed \
  -e "s|AGENTS_IMAGE|${ECR_BASE}/agents:latest|g" \
  -e "s|AGENTS_IRSA_ROLE_ARN|${IRSA_ARN}|g" \
  "${ROOT}/k8s/agents/research-agents.yaml" > "${ROOT}/.demo-deploy/agents/research-agents.yaml"
kubectl apply -f "${ROOT}/.demo-deploy/agents/"

echo "==> Deploying showcase UI..."
sed "s|UI_IMAGE|${ECR_BASE}/ui:latest|g" \
  "${ROOT}/k8s/showcase/ui.yaml" > "${ROOT}/.demo-deploy/showcase/ui.yaml"
"${ROOT}/scripts/apply-showcase-access.sh"

if [[ "${DEPLOYMENT_TIER}" == "self-hosted" ]]; then
  echo "==> Self-hosted LangSmith requires an Enterprise license."
  echo "    Skipping — use LangSmith Cloud with LANGSMITH_API_KEY instead."
fi

if [[ "${ENABLE_MONITORING}" == "true" ]]; then
  echo "==> Deploying monitoring..."
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
  helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
    -n monitoring --create-namespace \
    -f "${ROOT}/monitoring/helm-values.yaml" \
    --wait || echo "WARN: monitoring stack install failed"
else
  echo "==> Skipping Prometheus/Grafana (use LangSmith Cloud for tracing)."
fi

echo ""
echo "==> Deployment complete!"
if [[ -f "${ROOT}/.demo-access-mode" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.demo-access-mode"
  if [[ "${ACCESS_MODE:-}" == "ingress" ]]; then
    APP_DOMAIN="$(cdk_app_domain_name)"
    echo "    UI:        https://${APP_DOMAIN}  (Cognito auth at ALB)"
    echo "    Cognito:   $(cdk_cognito_hosted_ui_url)"
  else
    LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "(pending)")"
    echo "    UI:        http://${LB}"
    echo "    Cognito:   pool ready — ./scripts/cognito-create-user.sh (enable HTTPS: docs/COGNITO.md)"
  fi
else
  LB="$(kubectl get svc showcase-ui -n showcase -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "(pending)")"
  echo "    UI:        http://${LB}"
fi
echo "    LangSmith: https://smith.langchain.com  (project: ${LANGSMITH_PROJECT:-research-showcase})"
echo ""
echo "    When done demoing:  ./scripts/demo-stop.sh"
echo "    Check costs:        ./scripts/demo-status.sh"
