# Architecture Diagram

Visual reference for the **Kube RAG LangGraph** demo deployment (CDK + EKS).  
Open [architecture-diagram.html](./architecture-diagram.html) in a browser for an interactive view.

---

## 1. High-level deployment

What exists after `./scripts/infra-deploy.sh`, `./scripts/bootstrap.sh`, and `./scripts/deploy.sh`:

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
      NLB["Network Load Balancer<br/>showcase-ui Service"]

      subgraph eks["Amazon EKS · kube-rag-langg · K8s 1.32"]
        subgraph ns_showcase["namespace: showcase"]
          UI["Showcase UI<br/>React + FastAPI :8000"]
        end

        subgraph ns_agents["namespace: agents"]
          Agents["LangGraph Server<br/>research-agents"]
        end

        subgraph ns_rag["namespace: rag"]
          Ingest["Ingestion API"]
          Qdrant["Qdrant<br/>EBS 20Gi"]
          Redis["Redis<br/>(in-cluster)"]
        end

        subgraph ns_system["kube-system add-ons"]
          ALB["AWS Load Balancer Controller"]
          EBS["EBS CSI Driver"]
          CA["Cluster Autoscaler"]
          ESO["External Secrets Operator"]
        end

        Node["EC2 node group<br/>t3.large · general"]
      end

      S3["S3 bucket<br/>raw documents"]
      ECR["ECR repos<br/>agents · ingestion · ui"]
      SM["Secrets Manager"]
      IAM["IRSA roles<br/>Bedrock + S3"]
      Bedrock["Amazon Bedrock<br/>Claude + Titan Embeddings"]
    end
  end

  User -->|HTTPS/HTTP| NLB
  NLB --> UI
  UI -->|SSE / REST| Agents
  UI -->|upload| Ingest
  Agents -->|RAG query| Qdrant
  Agents -->|InvokeModel| Bedrock
  Agents -.->|LANGSMITH_API_KEY| LangSmith
  Agents -.->|optional| Tavily
  Ingest -->|chunk · embed · upsert| Qdrant
  Ingest -->|read/write| S3
  Ingest -->|embed| Bedrock
  CDK --> vpc
  CDK --> S3
  CDK --> ECR
  CDK --> SM
  CDK --> IAM
  IAM --> Agents
  IAM --> Ingest
  ECR -.->|pull images| Node
  Node --> UI
  Node --> Agents
  Node --> Ingest
  Node --> Qdrant
  Node --> Redis
  ALB --> NLB
  EBS --> Qdrant
  NAT --> Bedrock
  NAT --> LangSmith
```

---

## 2. Research request flow

```mermaid
sequenceDiagram
  actor User
  participant UI as Showcase UI<br/>(FastAPI)
  participant LG as LangGraph<br/>research-agents
  participant Q as Qdrant
  participant BR as Amazon Bedrock
  participant LS as LangSmith Cloud

  User->>UI: Enter research topic
  UI->>LG: POST /runs (SSE stream)
  LG->>Q: Vector search (RAG tool)
  Q-->>LG: Relevant chunks
  LG->>BR: Claude 3.5 Sonnet
  BR-->>LG: LLM response
  Note over LG: Coordinator → Researchers → Critic → Synthesizer
  LG-->>UI: Stream events / final report
  UI-->>User: Live progress + report
  LG-->>LS: Traces (API key)
```

---

## 3. Document ingestion flow

```mermaid
sequenceDiagram
  actor User
  participant UI as Showcase UI
  participant Ing as Ingestion API
  participant S3 as S3 bucket
  participant BR as Amazon Bedrock
  participant Q as Qdrant

  User->>UI: Upload PDF / text
  UI->>Ing: POST /ingest
  Ing->>S3: Store raw file
  Ing->>Ing: Chunk document
  Ing->>BR: Titan Embeddings
  BR-->>Ing: Vectors
  Ing->>Q: Upsert with metadata
  Ing-->>UI: Job complete
  UI-->>User: Ready for RAG queries
```

---

## 4. What CDK provisions vs what scripts deploy

| Layer | Script | Resources |
|-------|--------|-----------|
| **Infrastructure** | `infra-deploy.sh` | VPC, EKS cluster, node group, S3, ECR, IRSA roles, Secrets Manager |
| **Cluster add-ons** | `bootstrap.sh` | EBS CSI, ALB controller (+ IRSA), External Secrets, Cluster Autoscaler, StorageClass |
| **Applications** | `deploy.sh` | Qdrant, Redis, Ingestion, research-agents, Showcase UI + LoadBalancer |

### Demo mode defaults (cost-optimized)

| Included | Not included |
|----------|----------------|
| Single `t3.large` node pool (min 0) | RDS Postgres |
| In-cluster Redis | ElastiCache |
| In-cluster Qdrant on EBS | Self-hosted LangSmith |
| LangSmith Cloud (API key) | Prometheus/Grafana (optional) |

---

## 5. Network layout

```mermaid
flowchart LR
  subgraph public["Public subnets"]
    NLB2["NLB / ELB<br/>internet-facing"]
    NAT2["NAT Gateway"]
  end

  subgraph private["Private subnets"]
    EKS2["EKS worker nodes<br/>all app pods"]
  end

  Internet["Internet"] --> NLB2
  NLB2 --> EKS2
  EKS2 --> NAT2
  NAT2 --> Internet
```

- **Ingress:** `showcase-ui` LoadBalancer (internet-facing NLB via AWS Load Balancer Controller)
- **Egress:** NAT gateway for Bedrock, LangSmith, optional Tavily
- **Pods:** run on private subnets; no direct public IPs

---

## 6. IAM (IRSA)

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
    P1["bedrock:InvokeModel"]
    P2["s3:GetObject · PutObject"]
    P3["elasticloadbalancing:*"]
  end

  A --> R1 --> P1
  A --> R1 --> P2
  I --> R2 --> P1
  I --> R2 --> P2
  ALB_SA --> R3 --> P3
```

---

## 7. Demo lifecycle

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
    EKS + NAT, no nodes/LB
  end note

  note right of Running
    ~$185/mo if 24/7
    + Bedrock per use
  end note
```

---

## Related docs

- [BUILD_AND_USAGE.md](./BUILD_AND_USAGE.md) — deploy and operate
- [ARCHITECTURE.md](./ARCHITECTURE.md) — design decisions
- [DEMO_COSTS.md](./DEMO_COSTS.md) — cost by mode
