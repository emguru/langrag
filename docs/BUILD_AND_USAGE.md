# Build and Usage Guide

Complete instructions for building, deploying, operating, and using the **Kube RAG LangGraph** demo showcase on AWS.

**Architecture diagram:** [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) · [architecture-diagram.html](./architecture-diagram.html)

---

## Table of contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Architecture summary](#architecture-summary)
4. [Initial build and deploy](#initial-build-and-deploy)
5. [Configuration reference](#configuration-reference)
6. [Using the showcase](#using-the-showcase)
7. [Research agent workflow](#research-agent-workflow)
8. [RAG document ingestion](#rag-document-ingestion)
9. [Observability](#observability)
10. [Demo lifecycle (stop/start/park)](#demo-lifecycle-stopstartpark)
11. [Security (HTTPS, Cognito, guardrails)](#security-https-cognito-guardrails)
12. [Troubleshooting](#troubleshooting)
13. [Local development](#local-development)
14. [Upgrading and redeploying](#upgrading-and-redeploying)

---

## Overview

This project deploys a **multi-agent research team** (LangGraph) with **RAG** (retrieval-augmented generation) on **Amazon EKS**, using:

| Component | Technology |
|-----------|------------|
| Infrastructure | AWS CDK (TypeScript) → CloudFormation |
| Compute | Amazon EKS 1.32, `t3.large` node group |
| LLM + embeddings | Amazon Bedrock (Claude + Titan) |
| Vector store | Qdrant (in-cluster, EBS-backed) |
| Document store | Amazon S3 |
| Container registry | Amazon ECR |
| Tracing | LangSmith Cloud (API key) |
| UI | React + FastAPI gateway |
| Edge auth | Amazon Cognito @ ALB (HTTPS) |
| Guardrails | Upload cap, rate limits, SSRF block, NetworkPolicies |

**Demo mode** optimizes for intermittent internal demos: no RDS, no ElastiCache, single node pool, scale-to-zero support, and a **security-conscious** baseline (see [SECURITY.md](./SECURITY.md)).

---

## Prerequisites

### Tools

| Tool | Version | Install |
|------|---------|---------|
| AWS CLI | v2 | `brew install awscli` |
| Node.js | 18+ | `brew install node` |
| AWS CDK CLI | via npm | `npm install -g aws-cdk` (optional; project uses `npx`) |
| kubectl | latest | `brew install kubectl` |
| helm | 3.x | `brew install helm` |
| Docker | 24+ | Docker Desktop or `brew install docker colima && colima start` |
| git | any | — |

### AWS account

- IAM permissions for: CloudFormation, EKS, EC2, VPC, S3, ECR, IAM, Secrets Manager, Bedrock, Cognito, ACM, Budgets
- **Bedrock model access** enabled in the AWS console:
  - `us.anthropic.claude-sonnet-4-6` inference profile (Claude Sonnet 4.6)
  - `amazon.titan-embed-text-v2:0`
- Region: **us-east-1** (default; changeable)

### API keys

| Key | Required | Where |
|-----|----------|-------|
| `LANGSMITH_API_KEY` | Recommended | [smith.langchain.com](https://smith.langchain.com) — free tier works |
| `TAVILY_API_KEY` | Optional | Web search in research agents |
| `OPENAI_API_KEY` | Optional | Only if `LLM_PROVIDER=openai` |

No LangSmith self-hosted license is required.

---

## Architecture summary

```
Demo User
    │
    ▼ HTTPS + Cognito login
Application Load Balancer (showcase-ui Ingress)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Amazon EKS (kube-rag-langg)                                │
│  ┌─────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │ showcase    │  │ agents           │  │ rag           │  │
│  │ UI + API    │─▶│ LangGraph team   │─▶│ ingestion     │  │
│  │ (rate limit)│  │ research-agents  │  │ qdrant, redis │  │
│  └─────────────┘  └────────┬─────────┘  └───────┬───────┘  │
│       NetworkPolicies: UI → agents/ingestion only           │
└────────────────────────────┼────────────────────┼──────────┘
                             │                    │
                    Amazon Bedrock          Amazon S3
                    (model-scoped IRSA)     (documents)
                             │
                    LangSmith Cloud (traces)
```

See the full diagram: [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) or open [architecture-diagram.html](./architecture-diagram.html) in a browser.

### CDK-provisioned resources (`KubeRagLanggDemo` stack)

| Resource | Purpose |
|----------|---------|
| VPC | 3 AZs, public + private subnets, 1 NAT gateway |
| EKS cluster | `kube-rag-langg`, Kubernetes 1.32 |
| Node group | `general`, `t3.large`, min 0 / max 2 |
| S3 bucket | `{cluster}-documents-{account}` (encrypted, no public access) |
| ECR repos | `agents`, `ingestion`, `ui` |
| Cognito user pool | Admin-created users; OAuth for ALB auth |
| ACM certificate | TLS for `appDomainName` (DNS validation) |
| IAM (IRSA) | Model-scoped Bedrock + S3 for agents/ingestion pods |
| AWS Budget | Bedrock spend alerts (email) |
| Secrets Manager | App secrets placeholder |

### In-cluster workloads (`deploy.sh`)

| Namespace | Workloads |
|-----------|-----------|
| `showcase` | Showcase UI (React + FastAPI) |
| `agents` | LangGraph research team server |
| `rag` | Ingestion API, Qdrant, Redis |
| `kube-system` | EBS CSI, ALB controller (from bootstrap) |
| All app NS | NetworkPolicies (from `k8s/base/network-policies.yaml`) |

---

## Initial build and deploy

### Step 1 — Clone and configure

```bash
cd kube_rag_langg
cp .env.example .env
```

Edit `.env`:

```bash
LANGSMITH_API_KEY=lsv2_pt_...        # your key
AWS_REGION=us-east-1
CLUSTER_NAME=kube-rag-langg
LLM_PROVIDER=bedrock
```

Ensure AWS credentials are active:

```bash
aws sts get-caller-identity
```

### Step 2 — Deploy infrastructure (~15–20 min)

```bash
chmod +x scripts/*.sh
./scripts/infra-deploy.sh
```

This runs `cdk bootstrap` (if needed) and `cdk deploy` for stack `KubeRagLanggDemo`.

**Outputs** (also readable via `./scripts/demo-status.sh`):

- EKS cluster name
- S3 bucket
- ECR repository URIs
- IRSA role ARNs
- Cognito hosted UI URL
- ACM certificate ARN (when `appDomainName` is set)

### Step 2b — Create Cognito users

```bash
./scripts/cognito-create-user.sh you@company.com
```

Password policy: min 12 chars, upper + lower + digit. Self-sign-up is disabled.

### Step 2c — Enable HTTPS + Cognito at the ALB

Set your showcase domain in `cdk/cdk.json` (`appDomainName`) or run:

```bash
./scripts/enable-https-cognito.sh langrag.automatico.llc
```

Then complete **ACM DNS validation** at your domain registrar:

```bash
./scripts/print-acm-validation.sh
```

**GoDaddy** (`automatico.llc`): DNS → Manage → Add record → Type **CNAME**, Name the validation token host (without domain suffix), Value the `acm-validations.aws` target from the script output.

When the certificate status is **ISSUED**:

```bash
./scripts/apply-showcase-access.sh
```

Point your domain **CNAME** to the ALB hostname:

```bash
kubectl get ingress showcase-ui -n showcase
```

Users access `https://<appDomainName>/` → Cognito login → showcase UI.

Until the cert is issued, `apply-showcase-access.sh` falls back to a public HTTP LoadBalancer.

### Step 3 — Grant kubectl access (first time only)

If `kubectl get nodes` fails with *"must be logged in"*:

```bash
./scripts/grant-eks-access.sh
```

This enables `API_AND_CONFIG_MAP` auth and grants your IAM principal cluster admin.

### Step 4 — Bootstrap cluster add-ons (~5–10 min)

```bash
./scripts/bootstrap.sh
```

Installs:

- EBS CSI driver (Qdrant persistent volumes)
- AWS Load Balancer Controller
- External Secrets Operator
- Cluster autoscaler
- Base namespaces, `gp3` StorageClass, ConfigMaps

### Step 5 — Build images and deploy apps (~10–15 min)

```bash
./scripts/deploy.sh
```

This:

1. Logs in to ECR
2. Builds and pushes `agents`, `ingestion`, `ui` Docker images
3. Applies Kubernetes manifests with substituted image URIs and IRSA ARNs
4. Creates `app-secrets` in each namespace

At the end, note the **UI URL** (depends on access mode):

```bash
# HTTPS + Cognito (when ACM cert issued)
kubectl get ingress showcase-ui -n showcase

# HTTP LoadBalancer fallback
kubectl get svc showcase-ui -n showcase
```

### Step 6 — Verify

```bash
./scripts/demo-status.sh
kubectl get pods -n showcase -n agents -n rag
kubectl get networkpolicy -A
./scripts/validate-project.sh              # offline: pytest + SWAG integrity (~$0)
./scripts/validate-project.sh --live-smoke # + baseline search (~1 Titan embed)
curl -k https://langrag.automatico.llc/api/health   # after DNS + Cognito ingress
```

### Step 7 — Seed demo corpus (optional)

Load public RAG docs into `research_docs` without manual uploads:

```bash
./scripts/seed-demo-corpus.sh
```

Uses topic **`rag-demo`**. SWAG heuristic profiles are generated at ingest (no Bedrock). Then run the retrieval regression battery:

```bash
./scripts/benchmark-query-battery.sh   # 10 queries, scored eval sheet, exit 0 = PASS
```

---

## Configuration reference

### Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `CLUSTER_NAME` | `kube-rag-langg` | EKS cluster name |
| `LANGSMITH_API_KEY` | — | LangSmith Cloud tracing |
| `LANGSMITH_PROJECT` | `research-showcase` | Trace project name |
| `LLM_PROVIDER` | `bedrock` | `bedrock` or `openai` |
| `BEDROCK_REGION` | `us-east-1` | Bedrock region |
| `DEFAULT_LLM_MODEL` | Claude Sonnet 4.6 | Bedrock model ID (agents) |
| `DEFAULT_EMBEDDING_MODEL` | Titan Embed v2 | Bedrock embedding model |
| `CRAG_GRADER_MODEL` | Haiku 3 | CRAG relevance grader (ingestion) |
| `SWAG_PROFILE_MODE` | `heuristic` | `heuristic` (free) or `llm` (Bedrock profiles) |
| `TAVILY_API_KEY` | — | Optional web search |
| `MAX_RESEARCHER_PARALLEL` | `2` | Parallel researcher agents |
| `DEMO_MODE` | `true` | Demo sizing flag |
| `ENABLE_MONITORING` | `false` | Skip Prometheus/Grafana |

### CDK context (`cdk/cdk.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `clusterName` | `kube-rag-langg` | EKS cluster name |
| `adminPrincipalArn` | IAM user ARN | Auto-granted EKS admin on deploy |
| `cognitoDomainPrefix` | `kube-rag-langg-demo` | Cognito hosted UI prefix |
| `appDomainName` | `langrag.automatico.llc` | Showcase DNS name (enables ACM + ALB Cognito) |
| `certificateArn` | `""` | Optional existing ACM cert ARN |
| `budgetAlertEmail` | `michael@automatico.llc` | Bedrock spend alert recipient |
| `bedrockBudgetUsd` | `50` | Monthly Bedrock budget limit |
| `defaultLlmModel` | Claude 3.5 Sonnet ID | Agents IRSA Bedrock scope |
| `defaultEmbeddingModel` | Titan Embed v2 ID | Ingestion IRSA Bedrock scope |
| `demoMode` | `true` | Minimal infra |
| `nodeInstanceType` | `t3.large` | Worker instance type |
| `nodeMinSize` | `0` | Scale to zero when stopped |
| `nodeMaxSize` | `2` | Max nodes |
| `nodeDesiredSize` | `1` | Nodes at deploy time |

### API guardrails (ConfigMap / env)

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_UPLOAD_BYTES` | `104857600` (100 MB) | Max upload size |
| `RATE_LIMIT_INGEST_PER_MIN` | `10` (UI), `30` (ingestion) | Ingest requests per client IP |
| `RATE_LIMIT_RESEARCH_PER_MIN` | `2` (UI) | Research resume streams per client IP |
| `CORS_ORIGINS` | `https://langrag.automatico.llc` | Allowed browser origins |

---

## Using the showcase

### Access the UI

**With HTTPS + Cognito (recommended):**

1. Open `https://langrag.automatico.llc` (or your `appDomainName`)
2. Log in with your Cognito user (created via `cognito-create-user.sh`)

**Fallback (HTTP LoadBalancer, no edge auth):**

```bash
kubectl get svc showcase-ui -n showcase -o jsonpath='http://{.status.loadBalancer.ingress[0].hostname}'
```

### Run a research demo

1. **Enter a research topic** (e.g. *"Financial impact of a Strait of Hormuz closure"*).
2. **Optional — seed or upload documents** for RAG (see [RAG document ingestion](#rag-document-ingestion)).
3. Click **Start research run**.
4. **Plan review** — approve or edit sub-questions, then **Approve plan & continue**.
5. Watch **Live progress** while researchers run (~30–45s per iteration).
6. **Critique review** — choose **Synthesize report now** or **More research** (max 2 iterations).
7. **Synthesis** — Sonnet may take **60–90 seconds**; button shows *Synthesizing…*
8. **Report review** — edit the draft, then **Approve report**.
9. If the UI stalls after synthesis, **refresh the page** or use **Reload review panel** (report is saved in LangGraph thread state).
10. **View trace in LangSmith** (requires valid `LANGSMITH_API_KEY`).

### API endpoints (via UI gateway)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/ingest/file` | Upload document (multipart, max 100 MB, rate limited) |
| `POST` | `/api/research/stream` | Start research (SSE stream, rate limited) |

Direct ingestion API (in-cluster):

```bash
kubectl port-forward -n rag svc/ingestion 8001:8001
curl -X POST http://localhost:8001/ingest/url \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/doc.html","topic":"my-topic"}'
```

---

## Research agent workflow

The LangGraph graph (`agents/src/research_team/graph.py`) runs with **human-in-the-loop interrupts**:

```
START → Coordinator → [plan HITL] → Researchers (parallel) → Critic → [critique HITL]
     → Synthesizer → [report HITL] → END
```

| Agent | Role |
|-------|------|
| **Coordinator** | Decomposes topic into sub-questions |
| **Researchers** | Each investigates one sub-question using `rag_search` + optional `web_search` |
| **Critic** | Finds gaps, contradictions; decides if ready to synthesize |
| **Synthesizer** | Produces markdown report with citations (Sonnet 4.6; long runs ~60–90s) |

**HITL checkpoints:** `plan` · `critique` · `report` — each pauses until you resume via the UI.

### Tools available to researchers

| Tool | Description |
|------|-------------|
| `rag_search` | SWAG + CRAG via ingestion API (`swag_crag` when both enabled) |
| `web_search` | Tavily web search (requires `TAVILY_API_KEY`) |
| `trigger_ingestion` | Ingest a URL into the vector store mid-run |

### Tuning

In `.env` or ConfigMap:

- `MAX_RESEARCHER_PARALLEL=2` — fewer parallel agents = lower Bedrock cost
- `RAG_TOP_K=8` — chunks retrieved per query
- `CRAG_GRADER_MODEL` — Haiku by default (cheap grading)
- `SWAG_PROFILE_MODE=heuristic` — no Bedrock for SWAG profiles in demos
- `CHUNK_SIZE` / `CHUNK_OVERLAP` — ingestion granularity

---

## SWAG + CRAG retrieval

Augmented search runs inside the **ingestion API** (`POST /rag/search`). Same path for UI Search tab and agent `rag_search`.

| Mode | Behavior |
|------|----------|
| `baseline` | Titan embed + Qdrant only |
| `swag` | + heuristic/LLM profiles, rerank, noise gate, dedup |
| `crag` | + Haiku relevance grader |
| `swag_crag` | Full pipeline (agent default) |

**UI Search tab:** compare baseline vs augmented side-by-side. **Library tab** lists all documents in `research_docs` (not filtered by the research topic field).

**Eval regression:** edit `ingestion/benchmarks/query_eval_cases.json`, run `./scripts/benchmark-query-battery.sh` after deploy.

---

## RAG document ingestion

### Flow

```
Upload (UI) → S3 (raw) → Ingestion worker → chunk → classify kind → embed (Titan)
           → [heuristic or LLM SWAG profile] → Qdrant upsert
```

### Demo corpus (no upload)

```bash
./scripts/seed-demo-corpus.sh          # 5 public URLs → research_docs / rag-demo
./scripts/seed-demo-corpus.sh RECREATE=1   # wipe collection and re-ingest
```

### Supported formats

- PDF (`.pdf`)
- Plain text (`.txt`)
- Markdown (`.md`)
- HTML (`.html`)

### Collections

Documents are scoped by **topic** (metadata tag) and **collection** (default: `research_docs`). The **Library** tab shows all documents in the collection. Research agents search the full collection unless you add topic-scoped tooling later.

### Verify indexed documents

```bash
kubectl exec -n rag deploy/ingestion -- python3 -c "
import httpx
print(httpx.get('http://127.0.0.1:8001/rag/stats', params={'collection':'research_docs'}).json())
"
```

---

## Observability

### LangSmith Cloud

All agent LLM calls and graph steps are traced when `LANGSMITH_API_KEY` is set.

- Project: `research-showcase` (configurable)
- URL: [smith.langchain.com](https://smith.langchain.com)
- No self-hosted license required

### Kubernetes

```bash
# Pod status
kubectl get pods -A | grep -E 'showcase|agents|rag'

# Agent logs
kubectl logs -n agents -l app=research-agents -f

# Ingestion logs
kubectl logs -n rag -l app=ingestion -f
```

### Optional Prometheus/Grafana

Set `ENABLE_MONITORING=true` before `./scripts/deploy.sh` to install kube-prometheus-stack.

---

## Security (HTTPS, Cognito, guardrails)

This demo ships with a **security-conscious** baseline documented in [SECURITY.md](./SECURITY.md).

| Control | Summary |
|---------|---------|
| **HTTPS + Cognito @ ALB** | Login required before UI; TLS terminated at ALB |
| **Model-scoped IRSA** | Agents → LLM only; ingestion → embeddings only |
| **Upload cap** | 100 MB max per file |
| **Rate limits** | Per-IP limits on ingest and research endpoints |
| **SSRF protection** | URL ingest blocks private IPs and cloud metadata |
| **NetworkPolicies** | Showcase UI egress limited to agents + ingestion |
| **Bedrock budget** | Email alerts at 80% actual / 100% forecast spend |

### Quick commands

```bash
./scripts/enable-https-cognito.sh <domain>   # set domain + redeploy CDK
./scripts/print-acm-validation.sh            # ACM DNS validation CNAME
./scripts/apply-showcase-access.sh           # ALB ingress or HTTP LB fallback
./scripts/cognito-create-user.sh <email>       # admin-create user
```

`demo-stop.sh` removes the ALB ingress or LoadBalancer (saves ~$16–20/mo). Cognito pool and budget persist until `demo-park.sh`.

---

## Demo lifecycle (stop/start/park)

Designed for **intermittent demos** to minimize cost. See [DEMO_COSTS.md](./DEMO_COSTS.md).

| Action | Command | When |
|--------|---------|------|
| **Stop** (end of day) | `./scripts/demo-stop.sh` | Scale pods + nodes to 0, remove ALB/LB |
| **Start** (next demo) | `./scripts/demo-start.sh` | ~5 min warmup |
| **Status** | `./scripts/demo-status.sh` | Check state + cost estimate |
| **Park** (long break) | `./scripts/demo-park.sh` | `cdk destroy` — near-zero cost |

### Typical week

```bash
# Monday
./scripts/demo-start.sh

# ... run demos ...

# Friday
./scripts/demo-stop.sh

# Vacation
DEMO_PARK_CONFIRM=yes ./scripts/demo-park.sh
```

---

## Troubleshooting

### `kubectl` — "must be logged in" / credentials error

```bash
./scripts/grant-eks-access.sh
aws eks update-kubeconfig --region us-east-1 --name kube-rag-langg
kubectl get nodes
```

### `docker: command not found`

```bash
brew install docker colima
colima start --cpu 4 --memory 8
./scripts/deploy.sh
```

### Bedrock `AccessDeniedException`

Enable model access in **AWS Console → Bedrock → Model access**.

### Empty RAG results

1. Seed or upload documents first (`seed-demo-corpus.sh` or Ingest tab)
2. Use collection **`research_docs`** (only allowed collection in demo)
3. Do **not** rely on the research topic field to filter Search — it is for tagging uploads only
4. Check ingestion logs: `kubectl logs -n rag -l app=ingestion`

### Research stuck on "Synthesizing…"

Synthesis can take **60–90 seconds** on Sonnet for long reports. Check agent queue:

```bash
kubectl logs -n agents deploy/research-agents --tail=30 | grep -E 'n_running|Background run'
```

If `n_running=0` and logs show `Background run succeeded`, refresh the UI or call:

```bash
curl -s "https://langrag.automatico.llc/api/research/state?thread_id=<THREAD_ID>" | jq .checkpoint
```

Expected checkpoint after synthesis: **`report`**.

### LangSmith 401 warnings

Invalid or expired `LANGSMITH_API_KEY` logs warnings in agent/ingestion pods but does not block research. Update the key in Secrets Manager / `app-secrets` and restart pods.

### SWAG eval failures

```bash
./scripts/benchmark-query-battery.sh 2>/dev/null | tail -15
```

Tune expectations in `ingestion/benchmarks/query_eval_cases.json` or SWAG thresholds in `k8s/base/configmap.yaml`.

### Qdrant PVC pending

```bash
kubectl get storageclass gp3
kubectl get addon aws-ebs-csi-driver --cluster-name kube-rag-langg
./scripts/bootstrap.sh   # re-run if EBS CSI missing
```

### UI LoadBalancer or ingress pending

Wait 2–3 minutes. Ensure ALB controller is running:

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller
```

For Cognito ingress, ACM certificate must be **ISSUED** first: `./scripts/print-acm-validation.sh`

### Cognito login / redirect loop

- Callback URL must be `https://<appDomainName>/oauth2/idpresponse`
- User must exist and password set via `cognito-create-user.sh`
- If user status is `FORCE_CHANGE_PASSWORD`, set permanent password with the script again

### Rate limit or upload errors

- `413` — file exceeds 100 MB (`MAX_UPLOAD_BYTES`)
- `429` — too many requests; wait 60 seconds

### CDK deploy failures

| Error | Fix |
|-------|-----|
| `namespaces "rag" not found` | Fixed in CDK — namespaces created before IRSA |
| `unsupported Kubernetes version` | Use K8s 1.32 in `demo-stack.ts` |
| `clusterLogging parameter is required` | Do not pass empty `clusterLogging` |
| Orphaned S3 bucket | Delete bucket, redeploy |

Recovery script:

```bash
./scripts/recover-state.sh
./scripts/infra-deploy.sh
```

---

## Local development

Run without AWS (~$0):

```bash
cp .env.example .env
docker compose up -d
cd ui && npm install && npm run dev
```

| Service | URL |
|---------|-----|
| UI (dev) | http://localhost:3000 |
| API gateway | http://localhost:8000 |
| LangGraph | http://localhost:8123 |
| Ingestion | http://localhost:8001 |
| Qdrant | http://localhost:6333 |

LangGraph dev server:

```bash
cd agents
pip install -e ".[dev]"
langgraph dev
```

---

## Upgrading and redeploying

### App code only (no infra changes)

```bash
./scripts/deploy.sh
```

Rebuilds images, pushes to ECR, rolls out deployments.

### Infrastructure changes

```bash
cd cdk && npm run build && npx cdk diff
./scripts/infra-deploy.sh
```

### Full fresh deploy

```bash
./scripts/demo-park.sh          # destroy infra
./scripts/infra-deploy.sh
./scripts/grant-eks-access.sh   # if needed
./scripts/bootstrap.sh
./scripts/deploy.sh
```

### Update agent image only

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
docker build -t ${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/kube-rag-langg/agents:latest ./agents
docker push ${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/kube-rag-langg/agents:latest
kubectl rollout restart deploy/research-agents -n agents
```

---

## Related documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) | Architecture diagrams (Mermaid) |
| [architecture-diagram.html](./architecture-diagram.html) | Browser-viewable diagrams |
| [SECURITY.md](./SECURITY.md) | Auth, guardrails, data flows |
| [COGNITO.md](./COGNITO.md) | Cognito + ACM setup |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Design decisions |
| [DEMO_COSTS.md](./DEMO_COSTS.md) | Cost breakdown and lifecycle |
| [RUNBOOK.md](./RUNBOOK.md) | Operator quick reference |
| [../README.md](../README.md) | Project overview |

