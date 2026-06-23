# Demo Cost Guide

This deployment is tuned for **intermittent demos**, not 24/7 production.

## Three operating modes

| Mode | Command | When to use | ~Monthly cost |
|------|---------|-------------|---------------|
| **Running** | `./scripts/deploy.sh` | During a demo | ~\$185/mo if left on 24/7 |
| **Stopped** | `./scripts/demo-stop.sh` | Nights, weekends | ~\$73/mo EKS + NAT (~\$105) if cluster exists |
| **Parked** | `./scripts/demo-park.sh` | Weeks/months off | ~\$0-1/mo (S3/ECR only) |

**Important:** `demo-stop.sh` scales nodes to zero and removes the load balancer, but the **EKS control plane and NAT gateway keep billing**. Only `demo-park.sh` (CDK destroy) stops those charges.

Check current state anytime: `./scripts/demo-status.sh`

## What's included in demo defaults

To minimize cost, demo mode (`demo_mode = true` in Terraform) disables:

- **RDS Postgres** — agents use in-memory checkpoints (fine for demos; conversation state resets on pod restart)
- **ElastiCache Redis** — replaced with a tiny in-cluster Redis pod that only runs when the demo is running
- **Dedicated agent node pool** — everything runs on one `t3.large` node
- **Prometheus/Grafana** — skipped; use LangSmith Cloud for tracing (free tier with API key)
- **Self-hosted LangSmith** — not needed; Cloud API key only

## Cost breakdown (us-east-1)

### While running (~\$0.25/hour)

| Resource | Cost |
|----------|------|
| EKS control plane | \$73/mo (fixed) |
| NAT gateway | \$32/mo (fixed) |
| 1× t3.large EC2 | ~\$60/mo |
| LoadBalancer (NLB/ELB) | ~\$16/mo |
| EBS 20Gi (Qdrant) | ~\$2/mo |
| S3 + ECR | ~\$1/mo |
| **Bedrock** | per token (only cost during actual demos) |

**Tip:** A 2-hour demo costs roughly **\$0.50 in compute** plus Bedrock usage. The fixed \$105/mo base only applies while infrastructure exists.

### While stopped (`demo-stop.sh`)

| Still running | Cost |
|---------------|------|
| EKS control plane (standard support) | ~\$73/mo (~\$2.40/day) |
| NAT gateway | ~\$32/mo |
| EBS volumes (data preserved) | ~\$2/mo |
| LoadBalancer | **\$0** (deleted on stop) |
| EC2 nodes | **\$0** (scaled to 0) |

### EKS extended support (avoid this)

AWS **defaults new clusters to `EXTENDED` upgrade policy**. When your Kubernetes version leaves standard support, AWS keeps the cluster on that version and bills an **extra ~\$0.50/hr** (~\$360/mo) on top of the normal control-plane fee. Cost Explorer shows this as `USE1-AmazonEKS-Hours:extendedSupport`.

This project sets **`upgradePolicy: STANDARD`** via CDK (`EksStandardUpgradePolicy` custom resource in `cdk/lib/demo-stack.ts`) and again in `scripts/bootstrap.sh`. The cluster **auto-upgrades** at end of standard support instead of accruing extended-support charges.

If you ever see extended-support line items on a live cluster:

```bash
aws eks update-cluster-config --name kube-rag-langg \
  --upgrade-policy supportType=STANDARD
```

Or destroy with `demo-park.sh` when not demoing.

### While parked (`demo-park.sh`)

Terraform destroy removes EKS, VPC, NAT, and all compute. S3 documents and ECR images are kept so restart is fast.

## Recommended demo workflow

```bash
# First time setup (~20 min)
cp .env.example .env                    # add LANGSMITH_API_KEY
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
cd terraform && terraform apply
./scripts/bootstrap.sh
./scripts/deploy.sh

# ... run your demo ...

# End of day
./scripts/demo-stop.sh

# Next demo day (~5 min warmup)
./scripts/demo-start.sh

# Long break (vacation)
DEMO_PARK_CONFIRM=yes ./scripts/demo-park.sh
# Later:
cd terraform && terraform apply && ./scripts/bootstrap.sh && ./scripts/deploy.sh
```

## RDS auto-stop note

If you enable RDS (`enable_rds = true`), `demo-stop.sh` stops the instance. AWS auto-restarts stopped RDS after **7 days** — run `demo-stop.sh` again or accept the brief charge.

## LangSmith Cloud

Tracing uses [smith.langchain.com](https://smith.langchain.com) with your API key. The free/developer tier is sufficient for showcase demos. No Enterprise license required.

## Bedrock

You only pay when agents run. For a typical research demo (3 sub-questions, 1 synthesis):

- Claude 3.5 Sonnet: ~\$0.05–0.20 per run (varies by topic length)
- Titan Embeddings: ~\$0.001 per document batch
- CRAG grader (Haiku): ~\$0.03–0.08 extra per research run

Enable model access in the AWS Bedrock console before your first demo.

## Cost allocation tags and categories

Infrastructure is tagged for AWS Cost Explorer. CDK applies these tags on stack resources:

| Tag | Example | Purpose |
|-----|---------|---------|
| `Project` | `kube-rag-langg` | Primary project filter |
| `Environment` | `demo` | demo vs production |
| `Component` | `eks`, `compute`, `storage`, … | Per-resource cost bucket |
| `Workload` | `research-showcase` | Application workload |
| `CostCenter` | `research` | Chargeback / allocation |
| `Application` | `kube-rag-langg` | Alias for Project (Billing console) |
| `CostCategory` | `demo` | Environment grouping |
| `ManagedBy` | `cdk` | IaC ownership |

**Component values:** `networking` (VPC/NAT/ACM), `eks`, `compute` (node group), `storage` (S3/ECR), `auth` (Cognito), `secrets`, `ml-bedrock` (budget resource).

### One-time activation (Billing console)

Tags must be activated once per AWS account before they appear in Cost Explorer:

```bash
chmod +x scripts/apply-cost-tags-live.sh scripts/activate-cost-allocation-tags.sh

# 1) Tag existing resources (fast; no full CDK deploy)
./scripts/apply-cost-tags-live.sh

# 2) Activate tags + create Cost Category in Billing
./scripts/activate-cost-allocation-tags.sh
```

This script:

1. Activates all cost allocation tag keys (`Project`, `Component`, etc.)
2. Creates a **Cost Category** named `KubeRagLangg Workloads` with rules by Project and Component

Optional CDK context when deploying:

```bash
cd cdk && npx cdk deploy \
  -c costCenter=research \
  -c costOwner=you@example.com \
  -c environment=demo
```

After changing tags in CDK, redeploy infra so existing resources pick up `Component` tags:

```bash
./scripts/infra-deploy.sh
```

**Bedrock note:** Model inference is billed under the `Amazon Bedrock` service dimension, not resource tags. Use the stack’s Bedrock budget (set `budgetAlertEmail` in CDK context) plus Cost Explorer filter `Service = Amazon Bedrock` for token spend.
