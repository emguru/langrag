# Deployment Runbook (CDK Demo Mode)

> **Full guide:** [BUILD_AND_USAGE.md](BUILD_AND_USAGE.md) · **Architecture:** [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md)

## Pre-flight

- [ ] AWS credentials: `aws sts get-caller-identity`
- [ ] Node.js 18+: `node --version`
- [ ] Bedrock model access: Claude Sonnet 4.6 + Titan Embeddings v2 + Claude 3 Haiku (CRAG grader)
- [ ] LangSmith API key in `.env` (optional — 401 warnings if invalid, runs still work)
- [ ] `kubectl` access: `./scripts/grant-eks-access.sh` if needed

## First deploy

```bash
cp .env.example .env

./scripts/infra-deploy.sh    # CDK: VPC, EKS, S3, ECR (~15-20 min)
./scripts/bootstrap.sh       # ALB controller, external-secrets
./scripts/deploy.sh          # Build images, deploy to K8s
./scripts/seed-demo-corpus.sh
./scripts/validate-project.sh
./scripts/benchmark-query-battery.sh   # expect PASS (10/10)
```

## Daily demo workflow

```bash
./scripts/demo-start.sh      # morning
./scripts/demo-status.sh     # check
# ... demos ...
./scripts/demo-stop.sh       # evening — scale nodes to 0, drop ALB
```

## After code changes

| Change type | Command |
|-------------|---------|
| App only | `./scripts/deploy.sh` |
| ConfigMap only | `kubectl apply -f k8s/base/configmap.yaml && kubectl rollout restart deploy/ingestion deploy/research-agents -n rag -n agents` |
| SWAG tuning | edit `k8s/base/configmap.yaml` → apply → restart ingestion |
| Eval expectations | edit `ingestion/benchmarks/query_eval_cases.json` → `benchmark-query-battery.sh` |

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
| Bedrock access denied | Enable Sonnet 4.6, Titan v2, Haiku 3 in Bedrock console |
| Library empty but stats show docs | Refresh Library tab (topic filter bug fixed in UI — redeploy `ui` image) |
| Research "Synthesizing…" >2 min | Check `kubectl logs -n agents deploy/research-agents --tail=20`; refresh UI if run succeeded |
| SWAG eval FAIL | `./scripts/benchmark-query-battery.sh`; tune ConfigMap or `query_eval_cases.json` |
| LangSmith 401 | Update `LANGSMITH_API_KEY` in secrets; non-blocking |
| Stack outputs empty | Wait for `CREATE_COMPLETE`, then retry deploy.sh |

See [DEMO_COSTS.md](DEMO_COSTS.md) · [BUILD_AND_USAGE.md](BUILD_AND_USAGE.md#troubleshooting).
