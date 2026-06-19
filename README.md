# Kube RAG LangGraph — Demo Showcase on AWS (CDK)

Intermittent-demo deployment: **LangGraph research agents**, **LangSmith Cloud tracing**, and **RAG** on **Amazon EKS** — optimized to **stop/start cheaply**.

Infrastructure is managed with **AWS CDK** (TypeScript).

## Demo-first design

| Mode | Command | Idle cost |
|------|---------|-----------|
| **Running** | `./scripts/deploy.sh` | ~$0.25/hr compute |
| **Stopped** | `./scripts/demo-stop.sh` | ~$105/mo (EKS + NAT) |
| **Parked** | `./scripts/demo-park.sh` | ~$0-1/mo (S3 only) |

Full cost breakdown: [docs/DEMO_COSTS.md](docs/DEMO_COSTS.md)

## What you need

- AWS account + `aws` CLI, `kubectl`, `helm`, Docker, **Node.js 18+**
- **LangSmith API key** ([smith.langchain.com](https://smith.langchain.com)) — free tier, no license
- **Bedrock** model access enabled (Claude + Titan Embeddings)
- Optional later: `TAVILY_API_KEY` for web search

## Quick start

```bash
cp .env.example .env                          # LANGSMITH_API_KEY

# 1. Deploy AWS infrastructure (~15-20 min)
./scripts/infra-deploy.sh

# 2. Cluster add-ons + app workloads
./scripts/bootstrap.sh
./scripts/deploy.sh
```

Open the UI URL printed at the end.

## Stop / start

```bash
./scripts/demo-stop.sh    # end of demo day
./scripts/demo-start.sh   # next demo (~5 min warmup)
./scripts/demo-status.sh  # check state + cost estimate
./scripts/demo-park.sh    # long break — CDK destroy
```

## CDK project layout

```
cdk/
  bin/app.ts           CDK app entry
  lib/demo-stack.ts    VPC, EKS, S3, ECR, IRSA, Secrets Manager
  cdk.json             Context config (demoMode, node sizing)
scripts/
  infra-deploy.sh      cdk bootstrap + deploy
  cdk-outputs.sh       Read CloudFormation stack outputs
  deploy.sh            Build images + apply K8s manifests
  demo-stop.sh         Scale to zero
  demo-start.sh        Resume demo
  demo-park.sh         cdk destroy
```

### CDK context (cdk/cdk.json)

| Key | Default | Description |
|-----|---------|-------------|
| `clusterName` | `kube-rag-langg` | EKS cluster name |
| `demoMode` | `true` | Minimal single-node-pool setup |
| `nodeInstanceType` | `t3.large` | EC2 instance type |
| `nodeMinSize` | `0` | Scale to zero when stopped |
| `enableRds` | `false` | Skip RDS for demos |

Override at deploy time:
```bash
cd cdk && npx cdk deploy -c nodeDesiredSize=2
```

## Local dev (no AWS cost)

```bash
cp .env.example .env
docker compose up -d
cd ui && npm install && npm run dev
```

## Legacy Terraform

The `terraform/` directory is retained for reference but **CDK is the supported path**. Use `./scripts/infra-deploy.sh` instead of `terraform apply`.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) | **Architecture diagrams** (Mermaid) |
| [docs/architecture-diagram.html](docs/architecture-diagram.html) | Browser-viewable diagrams |
| [docs/BUILD_AND_USAGE.md](docs/BUILD_AND_USAGE.md) | **Full build & usage guide** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions |
| [docs/DEMO_COSTS.md](docs/DEMO_COSTS.md) | Cost & lifecycle |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Operator quick reference |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/RUNBOOK.md](docs/RUNBOOK.md).
