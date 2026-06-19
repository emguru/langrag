# Architecture

> **Diagram:** [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md) · [architecture-diagram.html](./architecture-diagram.html) · **Build & usage:** [BUILD_AND_USAGE.md](./BUILD_AND_USAGE.md)

## Goals

This deployment is a **showcase**, not a minimal POC. It prioritizes:

- **User input**: web UI for topics, document uploads, run configuration
- **Observability**: LangSmith traces, metrics, dashboards, run history
- **Scale path**: managed AWS data stores, horizontal agent scaling, async ingestion
- **Agent sophistication**: multi-agent research with RAG, not a single prompt chain

## Component decisions

### Why EKS?

LangSmith and LangGraph Deployment are officially supported on Kubernetes. EKS gives managed control plane, IRSA for pod-level AWS permissions, and integrates with ALB, RDS, and ElastiCache.

### Why Qdrant (not pgvector alone)?

For large document corpora, a dedicated vector database scales more predictably than pgvector on shared RDS. Qdrant runs well on EKS with persistent EBS volumes and supports filtering by metadata (topic, source, date).

PostgreSQL (RDS) is still used for LangGraph checkpoints, LangSmith metadata (if self-hosted), and application state.

### Why Bedrock + optional OpenAI?

Bedrock keeps data in AWS and avoids egress for inference. OpenAI is supported as a fallback for demos where Bedrock model access isn't enabled yet.

### LangSmith: Cloud vs self-hosted

| | LangSmith Cloud | Self-hosted |
|---|-----------------|-------------|
| License | API key (Plus/Enterprise) | Enterprise license key |
| Ops burden | None | ClickHouse, upgrades, ingress |
| Best for | Showcase, dev | Regulated / air-gapped |

This repo supports both via Helm value files in `k8s/langsmith/`.

## Data flows

### Research run

```
User → UI → FastAPI → LangGraph API
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
       Coordinator   RAG tool      Web search
            │             │             │
            └─────────────┼─────────────┘
                          ▼
                    Researchers (parallel)
                          ▼
                       Critic
                          ▼
                    Synthesizer → Report → UI
                          │
                          └── traces → LangSmith
```

### Document ingestion

```
Upload → UI/API → S3 (raw) → Ingestion worker
                                  │
                    chunk → embed (Bedrock/OpenAI)
                                  │
                              Qdrant upsert
                                  │
                    metadata → Postgres (job status)
```

## Security model

- **Network**: private subnets for workers; public subnets for ALB only
- **Secrets**: AWS Secrets Manager via External Secrets Operator
- **IAM**: IRSA roles per service (ingestion → S3, agents → Bedrock)
- **Ingress**: TLS via ACM; optional Cognito OIDC on ALB
- **Egress**: LangSmith self-hosted requires `https://beacon.langchain.com:443`

## Namespaces

| Namespace | Contents |
|-----------|----------|
| `langsmith` | LangSmith + LangGraph Deployment (Tier A) |
| `agents` | Research team LangGraph server |
| `rag` | Qdrant, ingestion workers |
| `showcase` | UI + API gateway |
| `monitoring` | Prometheus, Grafana |

## Scaling

- **Agents**: HPA on LangGraph API server CPU/memory; increase `researcher` parallelism in config
- **Ingestion**: KEDA on SQS queue depth (optional) or fixed worker replicas
- **Qdrant**: single-node for showcase; Qdrant cluster mode for production
- **EKS**: cluster autoscaler on node group min/max

## Future extensions

- Amazon OpenSearch as hybrid keyword + vector search
- Step Functions for long-running ingestion batches
- LangSmith eval datasets wired to automated regression on agent prompts
- Multi-tenant isolation via Kubernetes NetworkPolicies and per-tenant Qdrant collections
