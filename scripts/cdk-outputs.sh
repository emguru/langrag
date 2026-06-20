#!/usr/bin/env bash
# Read CloudFormation outputs from the CDK stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${CDK_STACK_NAME:-KubeRagLanggDemo}"
AWS_REGION="${AWS_REGION:-us-east-1}"

cdk_output() {
  local key="$1"
  aws cloudformation describe-stacks \
    --region "${AWS_REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
    --output text 2>/dev/null || echo ""
}

# Friendly aliases → CloudFormation output keys
cdk_cluster_name()       { cdk_output OutClusterName; }
cdk_node_group_name() {
  local raw
  raw="$(cdk_output OutNodeGroupName)"
  # EKS API expects the bare nodegroup name (e.g. "general"), not "cluster/general".
  if [[ "$raw" == */* ]]; then
    echo "${raw##*/}"
  else
    echo "$raw"
  fi
}
cdk_node_max_size()      { cdk_output OutNodeMaxSize; }
cdk_demo_mode()          { cdk_output OutDemoMode; }
cdk_agents_irsa_arn()    { cdk_output OutAgentsIrsaRoleArn; }
cdk_ingestion_irsa_arn() { cdk_output OutIngestionIrsaRoleArn; }
cdk_documents_bucket()   { cdk_output OutDocumentsBucket; }
cdk_rds_instance_id()    { cdk_output OutRdsInstanceId; }
cdk_kubeconfig_cmd()     { cdk_output OutKubeconfigCommand; }
cdk_cognito_user_pool_id()   { cdk_output OutCognitoUserPoolId; }
cdk_cognito_user_pool_arn()  { cdk_output OutCognitoUserPoolArn; }
cdk_cognito_client_id()      { cdk_output OutCognitoClientId; }
cdk_cognito_client_secret()  { cdk_output OutCognitoClientSecret; }
cdk_cognito_domain_prefix()  { cdk_output OutCognitoDomainPrefix; }
cdk_cognito_hosted_ui_url()  { cdk_output OutCognitoHostedUiUrl; }
cdk_app_domain_name()        { cdk_output OutAppDomainName; }
cdk_ingress_certificate_arn(){ cdk_output OutIngressCertificateArn; }
cdk_ingress_enabled()        { cdk_output OutIngressEnabled; }

export STACK_NAME AWS_REGION ROOT
