# Architecture

> **Diagram:** [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) · [architecture-diagram.html](./architecture-diagram.html) · **Build & usage:** [BUILD_AND_USAGE.md](./BUILD_AND_USAGE.md)

## Goals

This deployment is a **showcase**, not a minimal POC. It prioritizes:

- **User input**: web UI for topics, document uploads, HITL research review, direct vector search
- **Observability**: LangSmith traces, optional Prometheus, regression evals for SWAG retrieval
- **Scale path**: managed AWS data stores, horizontal agent scaling, async ingestion
- **Agent sophistication**: multi-agent research with augmented RAG (SWAG + CRAG), not a single prompt chain

## Component decisions

### Why EKS?

LangSmith and LangGraph Deployment are officially supported on Kubernetes. EKS gives managed control plane, IRSA for pod-level AWS permissions, and integrates with ALB, RDS, and ElastiCache.

### Why Qdrant (not pgvector alone)?

For large document corpora, a dedicated vector database scales more predictably than pgvector on shared RDS. Qdrant runs well on EKS with persistent EBS volumes and supports filtering by metadata (topic, source, chunk kind).

**SWAG uses the same Qdrant index** — no second vector store. Each chunk payload stores Titan vectors plus optional `swag_profile` metadata (domain terms, chunk kind, negatives).

### Why Bedrock + optional OpenAI?

Bedrock keeps data in AWS and avoids egress for inference. OpenAI is supported as a fallback for demos where Bedrock model access isn't enabled yet.

**Model split (defaults):**

| Role | Model | Where |
|------|-------|-------|
| Agent reasoning / synthesis | Claude Sonnet 4.6 | `agents` ConfigMap |
| Embeddings | Titan Embed v2 | `ingestion` |
| CRAG relevance grader | Claude 3 Haiku | `ingestion` (`CRAG_GRADER_MODEL`) |
| SWAG LLM profiles (optional) | Sonnet or Haiku | only when `SWAG_PROFILE_MODE=llm` |

Demo default: **`SWAG_PROFILE_MODE=heuristic`** — rule-based profiles at ingest and query time (zero Bedrock cost for SWAG reranking).

### LangSmith: Cloud vs self-hosted

| | LangSmith Cloud | Self-hosted |
|---|-----------------|-------------|
| License | API key (Plus/Enterprise) | Enterprise license key |
| Ops burden | None | ClickHouse, upgrades, ingress |
| Best for | Showcase, dev | Regulated / air-gapped |

This repo supports both via Helm value files in `k8s/langsmith/`. Invalid or missing `LANGSMITH_API_KEY` logs 401 warnings but does not block runs.

## SWAG augmentation layer

SWAG (Semantic Weighted Alignment for Generation) is a **sidecar ranking layer** on top of vector search:

- Does **not** edit source documents or treat synonym lists as facts
- Uses chunk text + heuristic (or optional LLM) profiles to score query–chunk alignment
- Applies chunk-type multipliers (`body`, `title`, `nav`, `boilerplate`) and noise gating
- Adaptive blend boosts SWAG weight on evaluative queries (e.g. “is rag useful”)

**Search pipeline** (`ingestion/src/ingestion/augmentation.py`):

```
Query → Titan embed → Qdrant retrieve (top-N candidates)
     → ensure SWAG profiles (heuristic or cached)
     → SWAG rerank + postprocess (dedup, diversity, noise gate, backfill)
     → [optional] CRAG grade (Haiku) → filter / rewrite / web fallback
```

**Modes:** `baseline` · `swag` · `crag` · `swag_crag` (agent default)

Regression eval: `ingestion/benchmarks/query_eval_cases.json` + `./scripts/benchmark-query-battery.sh` (10 fixed queries, pass threshold 80%).

## Data flows

### Research run (HITL)

```
User → UI → FastAPI SSE gateway → LangGraph API
                          │
            Coordinator → interrupt (plan review)
                          │
            Researchers (parallel, swag_crag via ingestion)
                          │
            Critic → interrupt (synthesize or more research)
                          │
            Synthesizer (Sonnet, ~60–90s for long reports)
                          │
            interrupt (report review) → approve → END
                          │
                          └── traces → LangSmith (if key valid)
```

Human-in-the-loop checkpoints: **plan** · **critique** · **report**. The UI streams LangGraph SSE; synthesis can take over a minute on Sonnet — the backend may finish before the browser shows the report (refresh or Reload review panel).

### Document ingestion

```
Upload / URL → UI or API → S3 (raw) → Ingestion worker
                                  │
                    chunk → classify chunk kind (heuristic)
                                  │
                    embed (Titan) + optional SWAG profile (heuristic or Haiku)
                                  │
                              Qdrant upsert (vectors + payload)
```

Demo corpus without uploads: `./scripts/seed-demo-corpus.sh` (public RAG READMEs → topic `rag-demo`).

### Direct search (UI Search tab)

Same `/rag/search` path as agents. Library tab lists all indexed documents (not filtered by research topic field). Supports baseline comparison side-by-side.

## Security model

- **Network**: private subnets for workers; public subnets for ALB only
- **Secrets**: AWS Secrets Manager via External Secrets Operator
- **IAM**: IRSA roles per service — model-scoped Bedrock (agents → Sonnet; ingestion → Titan + Haiku grader)
- **Ingress**: TLS via ACM; Cognito OIDC on ALB (`langrag.automatico.llc`)
- **Cost allocation**: CDK cost tags on stack resources (`Component`, `Workload`, etc.) — see [DEMO_COSTS.md](./DEMO_COSTS.md)

## Namespaces

| Namespace | Contents |
|-----------|----------|
| `langsmith` | LangSmith + LangGraph Deployment (optional Tier A) |
| `agents` | Research team LangGraph server |
| `rag` | Qdrant, ingestion API, Redis |
| `showcase` | UI + API gateway |
| `monitoring` | Prometheus, Grafana (optional) |

## Validation & regression

| Script | Purpose |
|--------|---------|
| `./scripts/validate-project.sh` | Offline go/no-go: pytest + SWAG integrity + heuristic rerank smoke |
| `./scripts/validate-project.sh --live-smoke` | + corpus stats + 1 baseline search (~1 Titan embed) |
| `./scripts/benchmark-query-battery.sh` | Live 10-query eval sheet vs demo corpus |
| `./scripts/seed-demo-corpus.sh` | Seed `research_docs` from public URLs |

## Scaling

- **Agents**: increase `MAX_RESEARCHER_PARALLEL`; HPA on LangGraph API server
- **Ingestion**: fixed replica; batch embed via `BATCH_SIZE`
- **Qdrant**: single-node + EBS for showcase; cluster mode for production
- **EKS**: cluster autoscaler on node group min/max (`nodeMinSize` 0 for scale-to-zero)

## Future extensions

- Amazon OpenSearch as hybrid keyword + vector search
- LangSmith eval datasets wired to `query_eval_cases.json` on every deploy
- Multi-tenant isolation via NetworkPolicies and per-tenant Qdrant collections
- Persistent LangGraph checkpoints (RDS) for production HITL sessions
