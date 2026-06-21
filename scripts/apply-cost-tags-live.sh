#!/usr/bin/env bash
# Apply cost allocation tags to live stack resources (no CDK deploy required).
#
# Use before activate-cost-allocation-tags.sh when new tag keys (Component, Workload, …)
# are not yet on any resource. After CDK changes, infra-deploy.sh is the durable path.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK_NAME:-KubeRagLanggDemo}"
PROJECT="${PROJECT:-kube-rag-langg}"
ENVIRONMENT="${ENVIRONMENT:-demo}"
WORKLOAD="${WORKLOAD:-research-showcase}"
COST_CENTER="${COST_CENTER:-research}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

component_for_arn() {
  local arn="$1"
  case "$arn" in
    *:budgets:*)
      echo "ml-bedrock"
      return
      ;;
    *:ec2:*:vpc/*|*:ec2:*:subnet/*|*:ec2:*:route-table/*|*:ec2:*:internet-gateway/*|*:ec2:*:natgateway/*|*:ec2:*:elastic-ip/*|*:ec2:*:security-group/*|*:ec2:*:vpc-endpoint/*)
      echo "networking" ;;
    *:ec2:*:instance/*)
      echo "compute" ;;
    *:eks:*:nodegroup/*)
      echo "compute" ;;
    *:eks:*)
      echo "eks" ;;
    *:s3:*)
      echo "storage" ;;
    *:ecr:*)
      echo "storage" ;;
    *:cognito-idp:*)
      echo "auth" ;;
    *:secretsmanager:*)
      echo "secrets" ;;
    *:budgets:*)
      echo "ml-bedrock" ;;
    *:acm:*)
      echo "networking" ;;
    *:elasticloadbalancing:*)
      echo "networking" ;;
    *:lambda:*|*:iam:*)
      echo "eks" ;;
    *)
      echo "infra" ;;
  esac
}

echo "==> Tagging CloudFormation stack: ${STACK}"
STACK_TAGS=(
  "Key=Project,Value=${PROJECT}"
  "Key=Environment,Value=${ENVIRONMENT}"
  "Key=Workload,Value=${WORKLOAD}"
  "Key=Application,Value=${PROJECT}"
  "Key=CostCategory,Value=${ENVIRONMENT}"
  "Key=CostCenter,Value=${COST_CENTER}"
  "Key=ManagedBy,Value=cdk"
)
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "+ aws cloudformation update-stack --stack-name ${STACK} --use-previous-template --tags ..."
else
  if ! aws cloudformation update-stack \
    --region "${REGION}" \
    --stack-name "${STACK}" \
    --tags "${STACK_TAGS[@]}" \
    --use-previous-template \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM >/dev/null 2>&1; then
    echo "    (stack tag update skipped — stack busy or unchanged; resource tags still apply)"
  fi
fi

echo "==> Tagging stack resources (tag filter aws:cloudformation:stack-name=${STACK})"
ARNS="$(
  aws resourcegroupstaggingapi get-resources \
    --region "${REGION}" \
    --tag-filters "Key=aws:cloudformation:stack-name,Values=${STACK}" \
    --query 'ResourceTagMappingList[].ResourceARN' \
    --output text | tr '\t' '\n' | sort -u
)"

# EKS cluster / nodegroup often lack CFN stack-name tag — add explicitly
CLUSTER="${PROJECT}"
EKS_ARNS=""
if aws eks describe-cluster --region "${REGION}" --name "${CLUSTER}" >/dev/null 2>&1; then
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
  EKS_ARNS="arn:aws:eks:${REGION}:${ACCOUNT}:cluster/${CLUSTER}"
  aws eks list-nodegroups --region "${REGION}" --cluster-name "${CLUSTER}" --output text 2>/dev/null \
    | tr '\t' '\n' | grep -v '^$' | while read -r ng; do
        aws eks describe-nodegroup \
          --region "${REGION}" \
          --cluster-name "${CLUSTER}" \
          --nodegroup-name "${ng}" \
          --query 'nodegroup.nodegroupArn' \
          --output text 2>/dev/null || true
      done | while read -r ng_arn; do
        [[ -n "${ng_arn}" && "${ng_arn}" != "None" ]] && echo "${ng_arn}"
      done >> /tmp/eks_arns.$$
  [[ -f /tmp/eks_arns.$$ ]] && EKS_ARNS="${EKS_ARNS}"$'\n'"$(cat /tmp/eks_arns.$$)" && rm -f /tmp/eks_arns.$$
fi

ALL_ARNS="$(printf '%s\n%s\n' "${ARNS}" "${EKS_ARNS}" | sort -u | grep -v '^$' || true)"
for arn in ${ALL_ARNS}; do
  [[ -z "${arn}" || "${arn}" == "None" ]] && continue
  component="$(component_for_arn "${arn}")"
  echo "    ${component}  ${arn##*/}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "+ aws resourcegroupstaggingapi tag-resources --resource-arn-list ${arn} --tags ..."
  else
    aws resourcegroupstaggingapi tag-resources \
      --region "${REGION}" \
      --resource-arn-list "${arn}" \
      --tags \
        "Project=${PROJECT},Environment=${ENVIRONMENT},Workload=${WORKLOAD},Application=${PROJECT},CostCategory=${ENVIRONMENT},CostCenter=${COST_CENTER},ManagedBy=cdk,Component=${component}" >/dev/null || {
        echo "    warning: could not tag ${arn}" >&2
      }
  fi
done

echo ""
echo "Done. Run ./scripts/activate-cost-allocation-tags.sh to activate new tag keys in Billing."
