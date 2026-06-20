# Security model (internal demo)

This demo uses a **security-conscious** baseline suitable for internal showcases — not full production hardening.

## Auth & edge access

| Mode | When | Behavior |
|------|------|----------|
| **HTTPS + Cognito (recommended)** | `appDomainName` set + ACM **ISSUED** | ALB terminates TLS; users must log in via Cognito before the UI |
| **HTTP LoadBalancer (fallback)** | No domain / cert pending | Public UI without edge auth; Cognito pool still exists for later |

Enable HTTPS + Cognito:

```bash
./scripts/enable-https-cognito.sh langrag.automatico.llc
# complete ACM DNS validation, then:
./scripts/apply-showcase-access.sh
./scripts/cognito-create-user.sh you@company.com
```

Domain configured in this demo: **`langrag.automatico.llc`** (GoDaddy DNS).

`demo-stop.sh` deletes the ALB ingress or NLB service (same as before). The Cognito pool persists until `demo-park.sh`.

## Data flows

```
User → [Cognito @ ALB] → showcase-ui (showcase ns)
                              ├→ research-agents:8000 (agents ns)
                              └→ ingestion:8001 (rag ns)
ingestion → S3 (documents) + Bedrock (embeddings) + Qdrant
agents    → Bedrock (LLM) + Qdrant + Redis + ingestion API
```

Documents are stored in a private S3 bucket. Vector search uses in-cluster Qdrant. LLM/embeddings use Bedrock via IRSA (no long-lived keys in pods).

## IAM (least privilege)

| Role | Bedrock access | Other |
|------|----------------|-------|
| **agents IRSA** | `InvokeModel*` on **LLM model only** | S3 read/write on documents bucket |
| **ingestion IRSA** | `InvokeModel` on **embedding model only** | S3 read/write on documents bucket |

Model IDs are set in `cdk/cdk.json` (`defaultLlmModel`, `defaultEmbeddingModel`) and must match `k8s/base/configmap.yaml`.

## API guardrails

| Control | Where | Default |
|---------|-------|---------|
| Upload size cap | UI gateway + ingestion | 100 MB |
| URL fetch size cap | ingestion `fetch_url` | 100 MB |
| Rate limits | UI: ingest 10/min, research 2/min per client IP | ingestion: 30/min |
| Collection allowlist | UI + ingestion | `research_docs` only |
| SSRF block | `POST /ingest/url` | Blocks private IPs, metadata, `.internal` hosts; validates redirect targets |

The UI does not expose URL ingest publicly; agents can trigger it internally (still SSRF-protected).

## Network policies

Applied from `k8s/base/network-policies.yaml`:

- **showcase-ui** → egress only to agents:8000, ingestion:8001, DNS
- **research-agents** → ingress from showcase; egress to rag + HTTPS (Bedrock, LangSmith)
- **ingestion** → ingress from showcase + agents; egress to Qdrant + HTTPS (S3, Bedrock, external URLs)
- **qdrant / redis** → ingress from agents/ingestion only

No impact on `demo-stop` / `demo-start` (policies stay; workloads scale to zero).

## Cost monitoring

Set in `cdk/cdk.json`:

```json
"budgetAlertEmail": "you@company.com",
"bedrockBudgetUsd": 50
```

Redeploy CDK to create an AWS Budget on **Amazon Bedrock** with email alerts at 80% actual spend and 100% forecast.

## Out of scope (by design)

- WAF, IP allowlists, mTLS
- VPC endpoints for Bedrock/S3 (uses NAT)
- EKS private-only API endpoint
- Secrets rotation, audit logging, SOC2 controls
- Multi-tenant isolation or per-user data boundaries
- Penetration testing / formal threat model

For production, add WAF, private endpoints, Pod Security Standards, External Secrets Operator, and org-level guardrails.

## Red team review (demo tier)

Last reviewed against ingress mode (`langrag.automatico.llc` + Cognito).

### What holds up

| Layer | Assessment |
|-------|----------------|
| **Edge** | TLS + Cognito when ingress mode is active; self-sign-up disabled |
| **Network** | NetworkPolicies restrict pod-to-pod paths; Qdrant/Redis have no public exposure |
| **API** | Upload cap, rate limits, SSRF blocks on URL ingest; URL fetch size capped |
| **IAM** | IRSA with model-scoped Bedrock; private S3 bucket |
| **Ops** | `demo-stop.sh` removes public LB; budget alerts on Bedrock |

### Residual risks (accepted at demo tier)

| Risk | Severity | Notes |
|------|----------|-------|
| **Edge-only auth** | High | Backend services (LangGraph, ingestion) trust the cluster network — no app-level bearer token |
| **Bedrock cost abuse** | High | Any Cognito user can run research; rate limit is 2 runs/min/IP (not per-user) |
| **LangGraph dev mode** | High | Agents run `langgraph dev` — use port-forward/RBAC carefully |
| **Cognito secret in ingress annotation** | Critical | Visible via `kubectl get ingress -o yaml`; `.demo-ingress.yaml` is gitignored |
| **Shared Qdrant collection** | Medium | Single `research_docs` collection for all demo users |
| **Prompt injection → tools** | Medium | Malicious PDFs can steer `trigger_ingestion` / web search |
| **In-memory rate limits** | Medium | Per-pod counters; scaling UI replicas multiplies limits |
| **HTTP fallback NLB** | Critical | Blocked by default when `appDomainName` is set unless `ALLOW_INSECURE_DEMO=true` |

### Remediations applied in this pass

- `.demo-ingress.yaml` / `.demo-access-mode` added to `.gitignore`
- Fail-closed deploy when domain is configured but ACM cert is not issued
- URL fetch capped at 100 MB (same as uploads)
- Collection picker removed; API allowlists `research_docs` only
- Research rate limit lowered to 2/min/IP
- LangSmith/API keys no longer synced to `showcase` namespace

### Human-in-the-loop & RAG visibility

| Feature | Where |
|---------|--------|
| **Plan approval** | Research tab — pause after coordinator; edit sub-questions before Bedrock spend |
| **Critique override** | Research tab — synthesize now vs more research |
| **Report edit** | Research tab — edit final report before done |
| **Ingest preview** | Ingest tab — chunk/quality preview before indexing |
| **Direct RAG search** | Search RAG tab — vector query only (embedding cost, no LLM agents) |
| **Vector library** | Library tab — browse documents/chunks, delete from Qdrant |

### Recommended next (still demo-tier)

1. **Internal bearer token** — shared secret on UI → agents/ingestion requests
2. **Replace `langgraph dev`** with production LangGraph server in `agents/Dockerfile`
3. **Redis-backed rate limits** — Redis is deployed but unused by app code
4. **Rotate Cognito client secret** if `.demo-ingress.yaml` was ever committed or shared
5. **Disable or gate `trigger_ingestion` tool** for untrusted document demos
6. **Baseline pod `securityContext`** — `runAsNonRoot`, drop capabilities

## Quick reference

| Script | Purpose |
|--------|---------|
| `./scripts/enable-https-cognito.sh <domain>` | Turn on TLS + Cognito at ALB |
| `./scripts/cognito-create-user.sh` | Admin-create demo users |
| `./scripts/demo-stop.sh` | Remove ingress/LB, scale to zero |
| `./scripts/infra-deploy.sh` | Apply CDK (IAM, budget, Cognito) |

See also: [COGNITO.md](./COGNITO.md), [DEMO_COSTS.md](./DEMO_COSTS.md).
