# langrag

Reference **retrieval-augmented generation** stack: ingest documents, retrieve with hybrid search, generate answers with citations.

The current path is **clean RAG on AWS** — S3 corpus, Qdrant on Fargate, Lambda ingest/agents, API Gateway, optional Cognito. A local Docker Compose path exists for development. An older EKS/Kubernetes showcase remains in-tree as a legacy option.

## What it demonstrates

- Document ingest, chunking, and embeddings
- Hybrid dense + lexical retrieval with reciprocal rank fusion
- Per-modality vector score floors (prose, markdown, code, tables)
- Optional Amazon Rerank 1.0 (not Cohere 3.5)
- LangGraph ask path with human-in-the-loop review
- Clickable citations in the demo UI
- Retrieval eval harnesses under `ingestion/benchmarks/`

Related library: [SWAG](https://github.com/emguru/swag) (portable rerank layer).

## Local development

```bash
cp .env.example .env
docker compose up -d
# Ingestion API: http://localhost:8001
cd ui && npm install && npm run dev
```

```bash
curl -s -X POST http://localhost:8001/rag/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"what is rag","top_k":5}'
```

## Deploy (clean RAG)

Requires AWS CLI, Node 18+, Docker, and Bedrock access (Titan Embeddings v2, Nova Lite or similar).

```bash
./scripts/infra-deploy-clean-rag.sh
API_ENDPOINT=<api gateway url> ./scripts/deploy-ui.sh
```

Details: [docs/DEPLOY_CLEAN_RAG.md](docs/DEPLOY_CLEAN_RAG.md).

## Layout

```
ingestion/   FastAPI ingest + /rag/search (chunk, embed, hybrid retrieve)
agents/      LangGraph RAG agent (/ask, /resume)
ui/          React demo (Ask / Search / Library / Ingest)
cdk/         AWS CDK (LangragCleanRag + LangragUi)
fixtures/    Demo and eval corpora
```

## Docs

| Document | Description |
|----------|-------------|
| [docs/DEPLOY_CLEAN_RAG.md](docs/DEPLOY_CLEAN_RAG.md) | Current AWS deploy |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design notes |
| [docs/SWAG_EVAL_HARNESS.md](docs/SWAG_EVAL_HARNESS.md) | Retrieval eval |
| [docs/SECURITY.md](docs/SECURITY.md) | Auth and boundaries |

## License

Source is provided for portfolio review. Production use in your environment is your responsibility; do not commit API keys or account credentials.
