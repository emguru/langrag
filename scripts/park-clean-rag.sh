#!/usr/bin/env bash
# Park the live RAG demo: keep S3 corpus + paused rag.automatico.llc page.
# Restore: cd cdk && npx cdk deploy LangragCleanRag --require-approval never
#          then API_ENDPOINT=... ./scripts/deploy-ui.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export CDK_DEFAULT_REGION="${CDK_DEFAULT_REGION:-$AWS_REGION}"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"

CLUSTER="langrag-clean-cluster"
SERVICE="LangragCleanRag-QdrantService39129681-Xfen1hd0rkOv"

echo "==> Scaling Fargate to 0 so the filesystem can detach"
if aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 >/dev/null
  aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE" || true
fi

echo "==> Parking CleanRag runtime (S3 corpus stays in the stack)"
cd "$ROOT/cdk"
npx cdk deploy LangragCleanRag \
  -c paused=true \
  --require-approval never \
  --outputs-file "$ROOT/cdk-outputs.json"

echo "==> Publishing paused page on rag.automatico.llc"
npx cdk deploy LangragUi \
  -c paused=true \
  -c "domainName=rag.automatico.llc" \
  -c "hostedZoneDomain=automatico.llc" \
  -c "hostedZoneId=Z03675412E5MU377HS9OE" \
  --require-approval never \
  --outputs-file "$ROOT/cdk-ui-outputs.json"

DIST_ID="$(aws cloudformation describe-stacks --stack-name LangragUi --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)"
if [[ -n "$DIST_ID" && "$DIST_ID" != "None" ]]; then
  aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null
fi

echo "==> Parked. Corpus bucket is DocsBucketName in cdk-outputs.json."
