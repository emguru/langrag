#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Destroying Kubernetes workloads..."
kubectl delete -f "${ROOT}/k8s/showcase/ui.yaml" --ignore-not-found
kubectl delete -f "${ROOT}/k8s/agents/research-agents.yaml" --ignore-not-found
kubectl delete -f "${ROOT}/k8s/rag/" --ignore-not-found

if [[ "${1:-}" == "--all" ]]; then
  echo "==> Destroying AWS infrastructure..."
  cd "${ROOT}/terraform"
  terraform destroy
fi

echo "==> Teardown complete."
