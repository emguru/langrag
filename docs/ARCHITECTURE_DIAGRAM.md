# Architecture Diagram

Visual reference for the **Kube RAG LangGraph** demo deployment (CDK + EKS).  
Open [architecture-diagram.html](./architecture-diagram.html) in a browser for an interactive view.

**Security model:** [SECURITY.md](./SECURITY.md)

---

## 1. High-level deployment (security-conscious demo)

What exists after `./scripts/infra-deploy.sh`, `./scripts/bootstrap.sh`, and `./scripts/deploy.sh` with HTTPS + Cognito enabled:

```mermaid
flowchart TB
  subgraph external["Outside AWS"]
    User["Demo user<br/>(browser)"]
    LangSmith["LangSmith Cloud<br/>traces & evals"]
    Tavily["Tavily API<br/>(optional web search)"]
  end

  subgraph aws["AWS Account · us-east-1"]
    CDK["CDK stack<br/>KubeRagLanggDemo"]

    subgraph vpc["VPC · 3 AZs"]
      NAT["NAT Gateway"]
      ALB["ALB · HTTPS :443<br/>Cognito auth @ edge"]

      subgraph eks["Amazon EKS · kube-rag-langg · K8s 1.32"]
        subgraph ns_showcase["namespace: showcase"]
          UI["Showcase UI<br/>React + FastAPI :8000"]
          NP1["NetworkPolicy<br/>egress → agents, ingestion"]
        end

        subgraph ns_agents["namespace: agents"]
          Agents["LangGraph Server<br/>research-agents"]
          NP2["NetworkPolicy<br/>ingress ← showcase"]
        end

        subgraph ns_rag["namespace: rag"]
          Ingest["Ingestion API<br/>SSRF guard · rate limits"]
          Qdrant["Qdrant<br/>EBS 20Gi"]
          Redis["Redis<br/>(in-cluster)"]
          NP3["NetworkPolicy<br/>ingress ← showcase, agents"]
        end

        subgraph ns_system["kube-system add-ons"]
          ALBC["AWS Load Balancer Controller"]
          EBS["EBS CSI Driver"]
          CA["Cluster Autoscaler"]
        end

        Node["EC2 node group<br/>t3.large · general"]
      end

      S3["S3 bucket<br/>raw documents · encrypted"]
      ECR["ECR repos<br/>agents · ingestion · ui"]
      SM["Secrets Manager"]
      Cognito["Cognito User Pool<br/>admin-created users"]
      Budget["AWS Budget<br/>Bedrock spend alerts"]
      IAM["IRSA · model-scoped<br/>LLM vs embedding"]
      Bedrock["Amazon Bedrock<br/>Claude + Titan Embeddings"]
    end
  end

  User -->|HTTPS| ALB
  ALB -->|authenticate-cognito| Cognito
  Cognito --> ALB
  ALB --> UI
  UI -->|SSE / REST| Agents
  UI -->|upload max 100MB| Ingest
  Agents -->|SWAG+CRAG via ingestion| Ingest
  Ingest -->|RAG query| Qdrant
  Agents -->|InvokeModel LLM only| Bedrock
  Agents -.->|LANGSMITH_API_KEY| LangSmith
  Agents -.->|optional| Tavily
  Ingest -->|chunk · embed · SWAG profile · upsert| Qdrant
  Ingest -->|SWAG rerank + CRAG grade| Bedrock
  Ingest -->|read/write| S3
  CDK --> vpc
  CDK --> S3
  CDK --> ECR
  CDK --> SM
  CDK --> Cognito
  CDK --> Budget
  CDK --> IAM
  IAM --> Agents
  IAM --> Ingest
  ECR -.->|pull images| Node
  Node --> UI
  Node --> Agents
  Node --> Ingest
  Node --> Qdrant
  ALBC --> ALB
  EBS --> Qdrant
  NAT --> Bedrock
  NAT --> LangSmith
  NP1 -.-> UI
  NP2 -.-> Agents
  NP3 -.-> Ingest
```

**Fallback:** If ACM certificate is not yet **ISSUED**, `apply-showcase-access.sh` uses a public HTTP LoadBalancer until DNS validation completes.

---

## 2. Retrieval pipeline (SWAG + CRAG)

Single Qdrant index — **no separate SWAG vector store**. Each chunk stores Titan vectors plus `swag_profile` metadata in the same payload.  
At ingest: vectors and profiles written in one pass (`SWAG_PROFILE_MODE=heuristic` by default — no Bedrock). At search: missing profiles enriched before rerank.  
Shared path for ingestion `/rag/search`, UI Search tab, and agent `rag_search` (default mode: `swag_crag`).

```mermaid
flowchart LR
  Q[Query] --> R[Vector retrieve · Qdrant]
  R --> P[Ensure SWAG profiles<br/>heuristic or cached]
  P --> S[SWAG rerank<br/>chunk kind · noise gate · dedup]
  S --> G[CRAG · Haiku grader]
  G -->|relevant| F[Return filtered chunks]
  G -->|ambiguous| RW[Rewrite query · re-retrieve]
  RW --> R
  G -->|irrelevant| W[Tavily web fallback · agents]
  W --> F
```

**Modes** (UI Search tab): `baseline` · `swag` · `crag` · `swag_crag` (default). Optional baseline comparison side-by-side.

**Key ConfigMap defaults** (`k8s/base/configmap.yaml`):

| Setting | Default | Notes |
|---------|---------|-------|
| `SWAG_PROFILE_MODE` | `heuristic` | Set `llm` for Bedrock chunk/query profiles |
| `CRAG_GRADER_MODEL` | Haiku 3 | Cheap relevance grading |
| `SWAG_MAX_CHUNKS_PER_SOURCE` | `3` | Diversity cap per document |
| `SWAG_NOISE_RELATIVE_VECTOR_RATIO` | `0.65` | Drop weak SWAG hits |
| `SWAG_NOISE_BACKFILL_RATIO` | `0.58` | Second-pass fill to reach `top_k` |

**Regression eval:** `./scripts/benchmark-query-battery.sh` scores 10 queries against `query_eval_cases.json` (80% pass threshold).

---

## 3. Research request flow

```mermaid
sequenceDiagram
  actor User
  participant ALB as ALB + Cognito
  participant UI as Showcase UI<br/>(FastAPI)
  participant LG as LangGraph<br/>research-agents
  participant Ing as Ingestion API
  participant Q as Qdrant
  participant BR as Amazon Bedrock
  participant LS as LangSmith Cloud

  User->>ALB: HTTPS request
  ALB->>User: Cognito login (if needed)
  User->>ALB: Authenticated session
  ALB->>UI: Forward to showcase-ui
  User->>UI: Enter research topic
  UI->>LG: POST /runs (SSE stream)
  LG->>Ing: POST /rag/search (swag_crag)
  Ing->>Q: Vector retrieve candidates
  Q-->>Ing: Chunks + SWAG metadata
  Ing->>Ing: Heuristic SWAG rerank
  Ing->>BR: Haiku CRAG grade
  BR-->>Ing: Graded chunks
  Ing-->>LG: Filtered hits or web_fallback
  LG->>BR: Claude Sonnet 4.6 synthesis
  BR-->>LG: LLM response
  Note over LG: plan HITL → researchers → critique HITL → synthesizer → report HITL
  LG-->>UI: SSE interrupts + report
  UI-->>User: Live progress + editable report
  LG-->>LS: Traces (API key)
```

---

## 4. Document ingestion flow

```mermaid
sequenceDiagram
  actor User
  participant ALB as ALB + Cognito
  participant UI as Showcase UI
  participant Ing as Ingestion API
  participant S3 as S3 bucket
  participant BR as Amazon Bedrock
  participant Q as Qdrant

  User->>ALB: HTTPS (authenticated)
  ALB->>UI: Forward
  User->>UI: Upload PDF / text (≤100 MB)
  UI->>UI: Rate limit check
  UI->>Ing: POST /ingest/file
  Ing->>S3: Store raw file
  Ing->>Ing: Chunk document
  Ing->>BR: Titan Embeddings
  BR-->>Ing: Vectors
  Ing->>Ing: Heuristic SWAG profiles + chunk kind
  Ing->>Q: Upsert vectors + SWAG payload
  Ing-->>UI: Job complete
  UI-->>User: Ready for RAG queries
```

**URL ingest** (`POST /ingest/url`) is cluster-internal only (agents tool); SSRF checks block private IPs and metadata endpoints.

**Demo seed (no upload):** `./scripts/seed-demo-corpus.sh` → topic `rag-demo`, collection `research_docs`.

---

## 5. What CDK provisions vs what scripts deploy

| Layer | Script | Resources |
|-------|--------|-----------|
| **Infrastructure** | `infra-deploy.sh` | VPC, EKS, S3, ECR, IRSA (model-scoped), Cognito, ACM cert, Bedrock budget, Secrets Manager |
| **Cluster add-ons** | `bootstrap.sh` | EBS CSI, ALB controller (+ IRSA), External Secrets, Cluster Autoscaler, StorageClass |
| **Applications** | `deploy.sh` | Qdrant, Redis, Ingestion, research-agents, Showcase UI, NetworkPolicies, ALB ingress or LB |

### Security-conscious demo defaults

| Included | Not included |
|----------|----------------|
| Cognito @ ALB (HTTPS) | WAF, IP allowlists |
| Model-scoped Bedrock IRSA | VPC endpoints for Bedrock/S3 |
| Upload cap + API rate limits | Formal pen test / SOC2 |
| SSRF block on URL ingest | Per-user data isolation |
| NetworkPolicies (UI → backends) | Private-only EKS API |
| Bedrock budget email alerts | — |

---

## 6. Network layout

```mermaid
flowchart LR
  subgraph public["Public subnets"]
    ALB2["ALB HTTPS :443<br/>Cognito authenticate"]
    NAT2["NAT Gateway"]
  end

  subgraph private["Private subnets"]
    EKS2["EKS worker nodes<br/>all app pods"]
  end

  Internet["Internet"] --> ALB2
  ALB2 --> EKS2
  EKS2 --> NAT2
  NAT2 --> Internet
```

- **Ingress:** ALB with TLS + Cognito (`langrag.automatico.llc` or your `appDomainName`)
- **Egress:** NAT gateway for Bedrock, LangSmith, optional Tavily
- **Pod traffic:** NetworkPolicies restrict showcase → agents/ingestion; qdrant/redis ingress from app pods only

---

## 7. IAM (IRSA)

```mermaid
flowchart LR
  subgraph pods["Kubernetes pods"]
    A["research-agents SA"]
    I["ingestion SA"]
    ALB_SA["aws-load-balancer-controller SA"]
  end

  subgraph roles["IAM roles"]
    R1["Agents IRSA role"]
    R2["Ingestion IRSA role"]
    R3["ALB controller role"]
  end

  subgraph perms["Permissions"]
    P1["bedrock:InvokeModel*<br/>Claude model only"]
    P2["bedrock:InvokeModel<br/>Titan embed + Haiku CRAG grader<br/>(+ optional SWAG llm mode)"]
    P3["s3:GetObject · PutObject"]
    P4["elasticloadbalancing:*"]
  end

  A --> R1 --> P1
  A --> R1 --> P3
  I --> R2 --> P2
  I --> R2 --> P3
  ALB_SA --> R3 --> P4
```

---

## 8. Demo lifecycle

```mermaid
stateDiagram-v2
  [*] --> Parked: initial / demo-park.sh
  Parked --> Running: infra-deploy + bootstrap + deploy
  Running --> Stopped: demo-stop.sh
  Stopped --> Running: demo-start.sh
  Running --> Parked: demo-park.sh
  Stopped --> Parked: demo-park.sh

  note right of Parked
    ~$0–1/mo
    S3 + ECR only
  end note

  note right of Stopped
    ~$105/mo
    EKS + NAT, no nodes/LB/ALB
  end note

  note right of Running
    ~$185–205/mo if 24/7
    ALB vs NLB ~+$0–20
    + Bedrock per use
  end note
```

`demo-stop.sh` removes ALB ingress or LoadBalancer; Cognito pool and budget persist until `demo-park.sh`.

---

## Related docs

- [BUILD_AND_USAGE.md](./BUILD_AND_USAGE.md) — deploy and operate
- [SECURITY.md](./SECURITY.md) — auth, guardrails, out of scope
- [COGNITO.md](./COGNITO.md) — Cognito + ACM setup
- [ARCHITECTURE.md](./ARCHITECTURE.md) — design decisions
- [DEMO_COSTS.md](./DEMO_COSTS.md) — cost by mode
