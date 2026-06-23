# Build and Usage Guide

Deploy, operate, and demo **Kube RAG LangGraph** on AWS — multi-agent research + augmented RAG on EKS.

**Diagrams:** [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) · [architecture-diagram.html](./architecture-diagram.html)

---

## Table of contents

1. [What this is](#what-this-is)
2. [Prerequisites](#prerequisites)
3. [Quick start (first deploy)](#quick-start-first-deploy)
4. [Configuration](#configuration)
5. [Using the showcase UI](#using-the-showcase-ui)
6. [Search RAG (SWAG · CRAG · expansion)](#search-rag-swag--crag--expansion)
7. [Research agents](#research-agents)
8. [Corpus & ingestion](#corpus--ingestion)
9. [Validation & benchmarks](#validation--benchmarks)
10. [Demo lifecycle & costs](#demo-lifecycle--costs)
11. [Security](#security)
12. [Troubleshooting](#troubleshooting)
13. [Local development](#local-development)
14. [Redeploy & upgrade](#redeploy--upgrade)

---

## What this is

| Piece | Technology |
|-------|------------|
| Infra | AWS CDK → EKS 1.35, VPC, S3, ECR, Cognito, ACM |
| Agents | LangGraph research team (Sonnet 4.6) |
| RAG | Qdrant + Titan embeddings + **SWAG** rerank + **CRAG** grading |
| UI | React + FastAPI gateway (HTTPS + Cognito) |
| Tracing | LangSmith Cloud |

**RAG** here is an architectural pattern, not a third-party product. This repo implements a specific flavor: heuristic SWAG (zero profile cost by default), Haiku CRAG, vector confidence floor, and same-document sibling expansion.

Portable SWAG library: separate project at `/Users/mike/SWAG` (local / on-prem / Docker). Kube currently vendors an adapted copy under `ingestion/src/ingestion/swag/`.

---

## Prerequisites

### Tools

AWS CLI v2 · Node 18+ · kubectl · helm · Docker (or Colima) · git

```bash
brew install awscli kubectl helm docker
colima start   # if not using Docker Desktop
```

### AWS

- Region default: **us-east-1**
- **Bedrock model access:** Claude Sonnet 4.6, Titan Embed v2, Claude 3 Haiku (CRAG)
- IAM for CloudFormation, EKS, EC2, S3, ECR, Cognito, Bedrock

### Keys (`.env`)

| Key | Required | Purpose |
|-----|----------|---------|
| `LANGSMITH_API_KEY` | Recommended | Agent traces |
| `TAVILY_API_KEY` | Optional | Web search in agents |
| `OPENAI_API_KEY` | Optional | Only if `LLM_PROVIDER=openai` |

---

## Quick start (first deploy)

### 1. Configure

```bash
cd kube_rag_langg
cp .env.example .env
# Edit LANGSMITH_API_KEY, AWS_REGION, CLUSTER_NAME
aws sts get-caller-identity
```

### 2. Infrastructure (~15–20 min)

```bash
chmod +x scripts/*.sh
./scripts/infra-deploy.sh
```

### 3. EKS access (first time)

```bash
./scripts/grant-eks-access.sh
aws eks update-kubeconfig --region us-east-1 --name kube-rag-langg
```

### 4. HTTPS + Cognito (recommended)

```bash
./scripts/cognito-create-user.sh you@company.com
./scripts/enable-https-cognito.sh langrag.automatico.llc
./scripts/print-acm-validation.sh          # add ACM CNAME at registrar
./scripts/apply-showcase-access.sh         # after cert ISSUED
```

Point domain CNAME → ALB from `kubectl get ingress showcase-ui -n showcase`.

### 5. Cluster bootstrap (~5–10 min)

```bash
./scripts/bootstrap.sh
```

Installs EBS CSI (with IRSA), ALB controller, autoscaler, namespaces, and sets EKS **STANDARD** upgrade policy (avoids extended-support billing).

### 6. Build & deploy apps (~10–15 min)

```bash
colima start    # if Docker not running
./scripts/deploy.sh
```

### 7. Seed demo corpus

```bash
./scripts/seed-demo-corpus.sh
# Or full reset:
RECREATE=1 ./scripts/seed-demo-corpus.sh
```

→ **112 chunks**, topic `rag-demo`, 5 public RAG URLs. Embed cost &lt; $0.001.

### 8. Verify

```bash
./scripts/demo-status.sh
./scripts/validate-project.sh
./scripts/validate-project.sh --live-smoke
./scripts/benchmark-query-battery.sh    # expect 10/10 PASS
```

---

## Configuration

### Core environment (`.env`)

| Variable | Default | Notes |
|----------|---------|-------|
| `CLUSTER_NAME` | `kube-rag-langg` | EKS cluster |
| `LLM_PROVIDER` | `bedrock` | Agents + ingestion |
| `SWAG_PROFILE_MODE` | `heuristic` | `heuristic` = $0 SWAG profiles |
| `CRAG_GRADER_MODEL` | Haiku 3 | CRAG grading |
| `RAG_MIN_VECTOR_SCORE` | `0.12` | Off-corpus confidence floor |
| `RAG_EXPAND_SAME_DOCUMENT` | `true` | Sibling chunk expansion |
| `RAG_SAME_DOCUMENT_MAX_PER_DOC` | `12` | Per-document expansion cap |

Full list: `.env.example` and `k8s/base/configmap.yaml`.

### Search modes (`POST /rag/search`)

| Mode | Behavior |
|------|----------|
| `baseline` | Titan embed + Qdrant top-k |
| `swag` | + profile rerank, noise gate, dedup, diversity |
| `crag` | + Haiku relevance grading, rewrite fallback |
| `swag_crag` | SWAG pool → CRAG (default for agents & UI) |

Set `compare: true` for baseline side-by-side + **lift** metrics (Search tab).

---

## Using the showcase UI

### Access

- **Production:** `https://langrag.automatico.llc` (or your `appDomainName`) → Cognito login
- **Fallback:** `kubectl get svc showcase-ui -n showcase`

### Tabs

| Tab | Purpose |
|-----|---------|
| **Research** | LangGraph HITL workflow (plan → research → critique → report) |
| **Search** | Try RAG modes, see lift cards, confidence banner |
| **Ingest** | Upload PDF/txt/md (≤100 MB) |
| **Library** | Documents in `research_docs` |

### Research flow (short)

1. Enter topic → **Start research run**
2. Approve plan → watch live progress
3. Critique → **Synthesize** (60–90s on Sonnet)
4. Edit & approve report

---

## Search RAG (SWAG · CRAG · expansion)

### Pipeline summary

1. Vector retrieve (baseline + larger candidate pool for SWAG)
2. **Confidence floor** — block if top score &lt; 0.12 (except CRAG web-fallback path)
3. **SWAG** — heuristic profiles, chunk-kind weights, noise gate
4. **CRAG** — Haiku grades `relevant` / `ambiguous` / `irrelevant`
5. **Sibling expansion** — if one chunk from a doc wins, add same-doc siblings from pool in reading order

### Example (live)

Query *“how does rag work”* in `swag_crag` → 3 chunks from same LangChain README (`document_id` shared), body chunk first, title + diagram as siblings.

### API (port-forward)

```bash
kubectl port-forward -n rag svc/ingestion 8001:8001
curl -s -X POST http://localhost:8001/rag/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"what is retrieval augmented generation","mode":"swag_crag","topic":"rag-demo","compare":true}' | jq '.hits | length, .expansion, .lift'
```

### Response fields

| Field | Meaning |
|-------|---------|
| `hits` | Final chunks |
| `swag` / `crag` | Pipeline metadata |
| `expansion` | Sibling expansion stats |
| `confidence` | Low-confidence block info |
| `lift` | Compare-mode improvement metrics |
| `baseline_hits` | Raw vector top-k when `compare: true` |

---

## Research agents

```
START → Coordinator → [plan HITL] → Researchers (parallel) → Critic → [critique HITL]
     → Synthesizer → [report HITL] → END
```

| Tool | Calls |
|------|-------|
| `rag_search` | Ingestion `/rag/search` (`swag_crag`) |
| `web_search` | Tavily (optional) |
| `trigger_ingestion` | URL → vector store |

Tune cost: `MAX_RESEARCHER_PARALLEL=2`, `SWAG_PROFILE_MODE=heuristic`.

---

## Corpus & ingestion

### Seed script (recommended for demos)

```bash
./scripts/seed-demo-corpus.sh
RECREATE=1 ./scripts/seed-demo-corpus.sh   # wipe + re-ingest
```

Sources: LangChain RAG README, AWS prescriptive RAG, Microsoft Generative AI lessons.

### Manual upload

UI Ingest tab or `POST /api/ingest/file` → S3 → chunk → embed → Qdrant.

### Stats

```bash
kubectl exec -n rag deploy/ingestion -- python3 -c "
from qdrant_client import QdrantClient
c = QdrantClient(url='http://qdrant.rag.svc.cluster.local:6333')
print('points:', c.get_collection('research_docs').points_count)
"
```

---

## Validation & benchmarks

| Command | Bedrock cost | Purpose |
|---------|--------------|---------|
| `validate-project.sh` | $0 | pytest + offline SWAG |
| `validate-project.sh --live-smoke` | ~1 embed | Cluster health + baseline search |
| `benchmark-query-battery.sh` | ~20 embeds | 10-query regression (80% pass) |

Edit expectations: `ingestion/benchmarks/query_eval_cases.json`.

---

## Demo lifecycle & costs

| Mode | Command | ~Monthly |
|------|---------|----------|
| **Running** | deploy + nodes up | ~$185/mo 24/7 + Bedrock use |
| **Stopped** | `demo-stop.sh` | ~$105/mo (EKS + NAT) |
| **Parked** | `demo-park.sh` | ~$0–1/mo |

```bash
./scripts/demo-stop.sh      # end of day
./scripts/demo-start.sh     # next demo
DEMO_PARK_CONFIRM=yes ./scripts/demo-park.sh   # vacation
./scripts/demo-status.sh
```

Details: [DEMO_COSTS.md](./DEMO_COSTS.md)

---

## Security

HTTPS + Cognito @ ALB · model-scoped IRSA · 100 MB upload cap · rate limits · SSRF block on URL ingest · NetworkPolicies.

```bash
./scripts/cognito-create-user.sh <email>
```

Full detail: [SECURITY.md](./SECURITY.md) · [COGNITO.md](./COGNITO.md)

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `kubectl` auth error | `./scripts/grant-eks-access.sh` |
| `docker: not found` | `colima start` |
| Bedrock AccessDenied | Enable models in Bedrock console |
| 0 search hits | Run `seed-demo-corpus.sh`; check topic `rag-demo` |
| Off-topic returns hits | Should be blocked — check `RAG_MIN_VECTOR_SCORE` |
| Only 1 chunk per doc | Enable `RAG_EXPAND_SAME_DOCUMENT`; use `swag_crag` |
| Synthesis stuck | Wait 60–90s; refresh UI; check agent logs |
| Qdrant PVC pending | Re-run `./scripts/bootstrap.sh` (EBS CSI IRSA) |
| SWAG eval FAIL | `./scripts/benchmark-query-battery.sh`; tune configmap |
| Extended support charges | Verify STANDARD upgrade policy in `demo-status.sh` |

---

## Local development

Without AWS (~$0 for SWAG tests):

```bash
cp .env.example .env
docker compose up -d
cd ui && npm install && npm run dev
```

| Service | URL |
|---------|-----|
| UI dev | http://localhost:3000 |
| Ingestion | http://localhost:8001 |
| Qdrant | http://localhost:6333 |

```bash
./scripts/validate-project.sh   # offline pytest
```

---

## Redeploy & upgrade

### App only (most common)

```bash
./scripts/deploy.sh
```

Rebuilds agents, ingestion, ui → ECR → rollout.

### Ingestion only (faster)

```bash
# After code changes under ingestion/
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
docker buildx build --platform linux/amd64 --push \
  -t ${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/kube-rag-langg/ingestion:latest ingestion
kubectl apply -f k8s/base/configmap.yaml
kubectl rollout restart deployment/ingestion -n rag
```

### Infrastructure

```bash
cd cdk && npm run build && npx cdk diff
./scripts/infra-deploy.sh
```

### Full reset

```bash
DEMO_PARK_CONFIRM=yes ./scripts/demo-park.sh
./scripts/infra-deploy.sh && ./scripts/bootstrap.sh && ./scripts/deploy.sh
./scripts/seed-demo-corpus.sh
```

---

## Related documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) | Mermaid diagrams |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Design rationale |
| [DEMO_COSTS.md](./DEMO_COSTS.md) | Cost breakdown |
| [SECURITY.md](./SECURITY.md) | Guardrails |
| [RUNBOOK.md](./RUNBOOK.md) | Operator checklist |
| [../README.md](../README.md) | Project overview |

Standalone SWAG library deploy guide: `/Users/mike/SWAG/docs/DEPLOY.md`
