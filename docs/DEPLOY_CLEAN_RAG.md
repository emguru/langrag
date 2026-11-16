# Clean RAG on AWS (no EKS)

Baseline RAG: **S3 → ingest/chunk/embed → Qdrant (Fargate) → Lambda agents (Bedrock)**.

## Parked (idle)

The live demo can be parked so Cognito, API, Lambdas, and Fargate are gone while **S3 corpus documents stay**.

```bash
./scripts/park-clean-rag.sh
```

Restore for a scheduled demo (corpus bucket stays in `LangragCleanRag`; do **not** pass `-c paused=true`):

```bash
export CDK_DEFAULT_ACCOUNT=<your-account> CDK_DEFAULT_REGION=us-east-1
cd cdk
npx cdk deploy LangragCleanRag --require-approval never --outputs-file ../cdk-outputs.json
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name LangragCleanRag \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
API_ENDPOINT="$API_ENDPOINT" ../scripts/deploy-ui.sh
```

Then re-ingest from the retained docs bucket (`DocsBucketName` output) so Qdrant is rebuilt. The login pool is recreated empty; add a demo user again.

## Architecture

- **S3** — raw documents
- **Qdrant on ECS Fargate** — vector store (EFS persistence)
- **Ingestion Lambda** — `/ingest/*`, `/rag/*` (FastAPI + Mangum)
- **Agents Lambda** — `/ask`, `/resume` (LangGraph RAG + optional HITL)
- **DynamoDB** — LangGraph checkpoints for HITL resume
- **API Gateway HTTP API** — single endpoint
- **LangSmith** (optional) — free-tier traces via `LANGSMITH_API_KEY`

No SWAG, CRAG, OpenSearch, or Kubernetes.

## Prerequisites

- AWS CLI (`aws login`) with Bedrock model access (Titan Embed v2, Nova Lite or similar)
- Node.js 18+, Docker
- LangSmith API key (optional)

## Local development

```bash
cp .env.example .env
docker compose up -d
# Ingestion: http://localhost:8001
# UI API: http://localhost:8000 (legacy ui-api; optional)
```

Seed demo corpus:

```bash
./scripts/seed-demo-corpus.sh
```

Test search:

```bash
curl -s -X POST http://localhost:8001/rag/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"what is rag","top_k":5}' | jq .
```

## Deploy to AWS

```bash
chmod +x scripts/infra-deploy-clean-rag.sh
./scripts/infra-deploy-clean-rag.sh
```

Stack name: **LangragCleanRag**. Estimated cost ~$15–35/mo + Bedrock usage.

Outputs:

- `ApiEndpoint` — base URL for API Gateway
- `DocsBucketName` — S3 corpus bucket
- `CheckpointTableName` — DynamoDB HITL checkpoints
- `QdrantUrl` — internal Cloud Map URL (ingestion Lambda only)

## Frontend (S3 + CloudFront)

Deploy the static UI independently (fast, no Lambda/Docker required):

```bash
./scripts/deploy-ui.sh
```

Outputs: `UiUrl` (CloudFront HTTPS URL), `UiBucketName`, `DistributionId`.

After the backend stack is deployed, wire API routes through CloudFront:

```bash
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name LangragCleanRag \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
API_ENDPOINT="$API_ENDPOINT" ./scripts/deploy-ui.sh
```

The UI uses same-origin paths (`/ask`, `/rag/search`, `/ingest/*`) proxied by CloudFront to API Gateway when `apiEndpoint` is set.

## API usage

### Search (direct RAG retrieval)

```bash
API=https://YOUR_API.execute-api.us-east-1.amazonaws.com
curl -s -X POST "$API/rag/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"your question","top_k":5}'
```

### Ask (LangGraph RAG agent)

```bash
curl -s -X POST "$API/ask" \
  -H 'Content-Type: application/json' \
  -d '{"question":"your question","hitl_enabled":false}'
```

### HITL resume

When `/ask` returns `"status":"interrupted"`, post approved chunks:

```bash
curl -s -X POST "$API/resume" \
  -H 'Content-Type: application/json' \
  -d '{"thread_id":"...","resume":[...chunks...]}'
```

## Tuning knobs

| Knob | Env var | Default |
|------|---------|---------|
| Top-k chunks | `RAG_TOP_K` | 5 |
| Chunk size | `CHUNK_SIZE` | 1000 |
| Chunk overlap | `CHUNK_OVERLAP` | 200 |
| Min vector score | `RAG_MIN_VECTOR_SCORE` | 0.12 |
| LLM model | `DEFAULT_LLM_MODEL` | `us.amazon.nova-lite-v1:0` |
| HITL on/off | `hitl_enabled` in `/ask` body | true |

Monitor runs in [LangSmith](https://smith.langchain.com) when `LANGSMITH_TRACING=true`.

## Eval baseline

```bash
./scripts/eval-rag-baseline.sh http://localhost:8001
```

## Legacy EKS path

The old `DemoStack` (EKS) remains available with `-c cleanRag=false`:

```bash
cd cdk && npx cdk deploy KubeRagLanggDemo -c cleanRag=false
```

For this branch, **clean RAG is the default**.
