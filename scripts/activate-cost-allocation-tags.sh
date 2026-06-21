#!/usr/bin/env bash
# Activate cost allocation tags in AWS Billing and create a Cost Category.
#
# Cost allocation tags must be activated once per account before they appear
# in Cost Explorer. Tags on resources are applied by CDK (see cdk/lib/cost-tags.ts).
#
# Usage:
#   ./scripts/activate-cost-allocation-tags.sh           # activate tags + create category
#   ./scripts/activate-cost-allocation-tags.sh --dry-run # print commands only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Cost Explorer / Billing APIs are only available in us-east-1
CE_REGION="us-east-1"
PROJECT="${PROJECT:-kube-rag-langg}"
CATEGORY_NAME="${CATEGORY_NAME:-KubeRagLangg Workloads}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

TAG_KEYS=(
  Project
  Environment
  Workload
  CostCenter
  Component
  Owner
  ManagedBy
  Application
  CostCategory
)

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "+ $*"
  else
    "$@"
  fi
}

echo "==> Activating cost allocation tags (region=${CE_REGION})"
FAILED=0
for key in "${TAG_KEYS[@]}"; do
  echo -n "    TagKey=${key} ... "
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "(dry-run)"
    run aws ce update-cost-allocation-tags-status \
      --region "${CE_REGION}" \
      --cost-allocation-tags-status "TagKey=${key},Status=Active"
    continue
  fi
  if out="$(aws ce update-cost-allocation-tags-status \
      --region "${CE_REGION}" \
      --cost-allocation-tags-status "TagKey=${key},Status=Active" 2>&1)"; then
    echo "active"
  elif [[ "$out" == *"Tag keys not found"* ]]; then
    echo "pending (AWS may take up to 24h after tagging — re-run tomorrow)"
    FAILED=$((FAILED + 1))
  else
    echo "error" >&2
    echo "$out" >&2
    exit 1
  fi
done

echo ""
if [[ "$FAILED" -gt 0 ]]; then
  echo "Warning: ${FAILED} tag key(s) could not be activated yet."
  echo "  ./scripts/apply-cost-tags-live.sh"
  echo "  ./scripts/activate-cost-allocation-tags.sh"
  echo ""
fi
echo "==> Creating Cost Category: ${CATEGORY_NAME}"

# Idempotent: skip if category already exists
if [[ "$DRY_RUN" -eq 0 ]]; then
  existing="$(aws ce list-cost-category-definitions \
    --region "${CE_REGION}" \
    --query "CostCategoryReferences[?Name=='${CATEGORY_NAME}'].CostCategoryArn | [0]" \
    --output text 2>/dev/null || echo "None")"
  if [[ -n "${existing}" && "${existing}" != "None" ]]; then
    echo "    Already exists: ${existing}"
    echo ""
    echo "Done. View costs in Cost Explorer:"
    echo "  https://console.aws.amazon.com/cost-management/home#/cost-explorer"
    echo "  Group by: Cost category → ${CATEGORY_NAME}"
    echo "  Or filter by tag: Project = ${PROJECT}"
    exit 0
  fi
fi

RULES="$(cat <<'EOF'
[
  {
    "Value": "Project-kube-rag-langg",
    "Type": "REGULAR",
    "Rule": {
      "Tags": {
        "Key": "Project",
        "Values": ["kube-rag-langg"],
        "MatchOptions": ["EQUALS"]
      }
    }
  },
  {
    "Value": "Compute-EKS",
    "Type": "REGULAR",
    "Rule": {
      "Tags": {
        "Key": "Component",
        "Values": ["eks", "compute"],
        "MatchOptions": ["EQUALS"]
      }
    }
  },
  {
    "Value": "Networking",
    "Type": "REGULAR",
    "Rule": {
      "Tags": {
        "Key": "Component",
        "Values": ["networking"],
        "MatchOptions": ["EQUALS"]
      }
    }
  },
  {
    "Value": "Storage",
    "Type": "REGULAR",
    "Rule": {
      "Tags": {
        "Key": "Component",
        "Values": ["storage"],
        "MatchOptions": ["EQUALS"]
      }
    }
  },
  {
    "Value": "Auth",
    "Type": "REGULAR",
    "Rule": {
      "Tags": {
        "Key": "Component",
        "Values": ["auth"],
        "MatchOptions": ["EQUALS"]
      }
    }
  },
  {
    "Value": "Secrets",
    "Type": "REGULAR",
    "Rule": {
      "Tags": {
        "Key": "Component",
        "Values": ["secrets"],
        "MatchOptions": ["EQUALS"]
      }
    }
  }
]
EOF
)"

run aws ce create-cost-category-definition \
  --region "${CE_REGION}" \
  --name "${CATEGORY_NAME}" \
  --rule-version "CostCategoryExpression.v1" \
  --rules "${RULES}" \
  --default-value "Uncategorized"

echo ""
echo "Done."
echo ""
echo "Notes:"
echo "  • New tags can take up to 24 hours to appear in Cost Explorer."
echo "  • Re-deploy infra to apply Component tags to existing resources:"
echo "      ${ROOT}/scripts/infra-deploy.sh"
echo "  • Bedrock usage is billed by service, not resource tag — use the existing"
echo "    Bedrock budget filter (Service = Amazon Bedrock) for model spend."
echo ""
echo "Cost Explorer:"
echo "  https://console.aws.amazon.com/cost-management/home#/cost-explorer"
