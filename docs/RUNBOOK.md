# Deployment Runbook (CDK Demo Mode)

> **Full guide:** [BUILD_AND_USAGE.md](BUILD_AND_USAGE.md)

## Pre-flight

- [ ] AWS credentials: `aws sts get-caller-identity`
- [ ] Node.js 18+: `node --version`
- [ ] Bedrock model access enabled (Claude 3.5 Sonnet + Titan Embeddings v2)
- [ ] LangSmith API key in `.env` (Cloud — no license needed)

## First deploy

```bash
cp .env.example .env

./scripts/infra-deploy.sh    # CDK: VPC, EKS, S3, ECR (~15-20 min)
./scripts/bootstrap.sh       # ALB controller, external-secrets
./scripts/deploy.sh          # Build images, deploy to K8s
```

## Daily demo workflow

```bash
./scripts/demo-start.sh      # morning
./scripts/demo-status.sh     # check
./scripts/demo-stop.sh       # evening
```

## Long break

```bash
DEMO_PARK_CONFIRM=yes ./scripts/demo-park.sh

# Return later
./scripts/infra-deploy.sh && ./scripts/bootstrap.sh && ./scripts/deploy.sh
```

## CDK commands (manual)

```bash
cd cdk
npm install
npx cdk synth          # validate template
npx cdk diff           # preview changes
npx cdk deploy         # deploy stack
npx cdk destroy        # tear down
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| CDK bootstrap error | `cd cdk && npx cdk bootstrap aws://ACCOUNT/REGION` |
| Pods pending | `./scripts/demo-start.sh` — nodes may be at 0 |
| Bedrock access denied | Enable models in AWS Bedrock console |
| Stack outputs empty | Wait for `CREATE_COMPLETE`, then retry deploy.sh |

See [DEMO_COSTS.md](DEMO_COSTS.md).
