# Tuning Plan — Kube RAG LangGraph

Cost-conscious workflow for improving SWAG / CRAG retrieval without burning Bedrock tokens or leaving EKS idle.

**Related:** [BUILD_AND_USAGE.md](./BUILD_AND_USAGE.md) · [DEMO_COSTS.md](./DEMO_COSTS.md) · [ARCHITECTURE_DIAGRAM.md](./ARCHITECTURE_DIAGRAM.md)

---

## Where the project stands

| Piece | Status |
|-------|--------|
| **Live demo** | `langrag.automatico.llc` — Search, Ingest, Research tabs |
| **Corpus tiers** | Tier 1 RAG lesson (~112 chunks) · Tier 2 batch seed (23 URLs, 6 topics) · Tier 3 your uploads |
| **Stack** | Titan embed · heuristic SWAG ($0 profiles) · Haiku CRAG · topic filter · symmetric baseline |
| **Regression** | `benchmark-query-battery.sh` — 14 cases (10 SWAG + 4 cross-domain/CRAG), 80% pass bar |
| **Offline gate** | `validate-project.sh` — pytest + SWAG integrity, $0 |

---

## What costs money (and what does not)

| Activity | Bedrock / AWS | Use for |
|----------|---------------|---------|
| `validate-project.sh` | **$0** | Every code change |
| `benchmark-query-battery.sh` | ~20–30 Titan embeds (~**$0.0001**) | After config/SWAG knob changes |
| Search **Vector / SWAG** | ~1 embed/query | **Daily tuning** |
| Search **SWAG→CRAG** | embed + Haiku | Spot-check only (5–10 queries) |
| `seed-demo-corpus.sh` / batch seed | Titan only (~**$0.001** full batch) | One-time / append |
| Research agents | **$0.05–0.20+** / run | Demo prep — **not** retrieval tuning |
| EKS running 24/7 | ~**$185/mo** | Avoid — use `demo-stop.sh` |
| EKS stopped | ~**$105/mo** | Nights/weekends |
| EKS parked | ~**$0–1/mo** | Weeks off |

**Default demo config:** `SWAG_PROFILE_MODE=heuristic` — no Bedrock LLM for SWAG profiles at ingest or query time.

---

## Corpus seeding plan (three tiers)

### Tier 1 — RAG lesson eval (regression gate)

**Purpose:** Prove SWAG beats title/nav pollution on README-heavy lesson HTML.

```bash
./scripts/demo-start.sh
./scripts/seed-demo-corpus.sh              # append; RECREATE=1 only to wipe
./scripts/benchmark-query-battery.sh       # expect PASS (10/10 SWAG cases)
```

~112 chunks · 5 public URLs · topic `rag-demo` · embed cost &lt; $0.001.

### Tier 2 — Cross-domain stress (showcase value)

**Purpose:** Mixed-folder retrieval — topic filter, CRAG, lexical rescue.

```bash
DRY_RUN=1 ./scripts/seed-corpus-batch.sh   # always plan first
./scripts/seed-corpus-batch.sh             # SKIP_EXISTING=1 (default)
```

23 URLs · 6 topics · append-only · see `scripts/corpus-batch.manifest`.

Battery cases (after batch seed):

| Case ID | Query | Mode | Topic |
|---------|-------|------|-------|
| `automate-rag-cross` | how do we automate rag | SWAG→CRAG | All |
| `three-way-mixed` | three-way matching purchase order | SWAG | All |
| `invoice-ap-topic` | invoice processing automation | SWAG | ap-procurement |
| `invoice-cafeteria-noise` | invoice cafeteria menu | SWAG→CRAG | All |
| `pancakes-keyword` / `pancackes-typo` | pancakes / pancackes | SWAG→CRAG | All |
| `off-corpus-shoe` | what is a shoe | SWAG | All |
| `openai-playground` | what is an openai playground | SWAG | All |
| `cafeteria-food-safety` | food safety basics… | SWAG | cafeteria-ops |

Recipe typo cases require a user-uploaded recipe doc in corpus (upload via Ingest tab).

### Tier 3 — Your documents (proof on real content)

Upload 3–5 docs via UI Ingest (SOPs, recipes, AP guides). Each doc costs pennies in Titan embeds.

Add one eval case per doc in `ingestion/benchmarks/query_eval_cases.json` using **stable text markers** (not document UUIDs).

---

## Tuning loop (do this, not pocket pool)

### Session start

```bash
./scripts/demo-start.sh
./scripts/demo-status.sh
```

### After any code change

```bash
./scripts/validate-project.sh            # $0
```

### After ConfigMap / SWAG knob change

```bash
kubectl apply -f k8s/base/configmap.yaml
kubectl rollout restart deploy/ingestion -n rag
./scripts/benchmark-query-battery.sh     # ~$0.0001
```

### UI iteration (cheap)

1. Search tab → mode **SWAG** (not SWAG→CRAG)
2. **Compare against baseline** ON
3. Topic filter as needed
4. Tune one knob at a time; re-run battery between changes

### CRAG validation (expensive — batch it)

Run once per tuning session, not per search:

- `automate-rag-cross` query
- `invoice-cafeteria-noise` query
- Your typo/keyword uploads (`pancackes` if recipe ingested)

Mode **SWAG→CRAG** in UI or rely on battery CRAG cases.

### Session end

```bash
./scripts/demo-stop.sh
```

---

## Config knobs worth tuning

Edit `k8s/base/configmap.yaml` (rag namespace) — no LLM cost for SWAG rerank itself.

| Variable | Default | Effect |
|----------|---------|--------|
| `RAG_MIN_VECTOR_SCORE` | 0.12 | Off-corpus confidence floor (all topics) |
| `RAG_MIN_VECTOR_SCORE_TOPIC` | 0.08 | Lower floor when topic filter narrows corpus |
| `SWAG_CHUNK_TITLE_MULTIPLIER` | 0.40 | Title down-rank at rerank |
| `SWAG_NOISE_RELATIVE_VECTOR_RATIO` | 0.65 | Noise gate strictness |
| `SWAG_MAX_CHUNKS_PER_SOURCE` | 3 | Source diversity cap |
| `CRAG_LEXICAL_RESCUE` | true | Typo/keyword rescue after CRAG clears all |
| `RAG_FILTER_NON_BODY_HITS` | true | Body-only final hits |
| `RAG_COMPARE_SYMMETRIC_BASELINE` | true | Honest lift in compare panel |

Expectations live in `ingestion/benchmarks/query_eval_cases.json`.

---

## Two-week sprint (recommended)

### Week 1 — Lock the harness

1. Ensure Tier 1 + Tier 2 seeded (`seed-demo-corpus.sh` + `seed-corpus-batch.sh`).
2. Run battery until **PASS** (≥80% of non-optional cases).
3. Upload Tier 3 docs; add eval cases if you want them in CI.
4. `demo-stop.sh` every night.

### Week 2 — Tune SWAG on Tier 2

1. Change **one** ConfigMap knob per iteration.
2. Battery after each change (Tier 1 gate stays green).
3. Manual Tier 2 spot-checks in SWAG mode.
4. CRAG spot-check once per knob — not every search.
5. Record wins in docs / `swag.html` only when battery + spot-check agree.

### Demo week

```bash
./scripts/demo-start.sh
# One research run to show agents + RAG end-to-end
./scripts/demo-stop.sh
```

---

## Commands cheat sheet

| Command | Cost | When |
|---------|------|------|
| `./scripts/validate-project.sh` | $0 | Every code change |
| `./scripts/validate-project.sh --live-smoke` | ~1 embed | Cluster health |
| `./scripts/benchmark-query-battery.sh` | ~$0.0001 | After tuning |
| `DRY_RUN=1 ./scripts/seed-corpus-batch.sh` | $0 | Before batch ingest |
| `./scripts/demo-stop.sh` | — | End of day |
| `./scripts/demo-start.sh` | — | Tuning session |
| `DEMO_PARK_CONFIRM=yes ./scripts/demo-park.sh` | — | Weeks off |

---

## What not to do

- Do **not** use research agents to tune retrieval — use Search tab.
- Do **not** use SWAG→CRAG as your default tuning mode — Haiku adds up.
- Do **not** `RECREATE=1` batch seed unless you intend to wipe the collection.
- Do **not** leave the cluster running when you are not working.

---

## Adding a new eval case

1. Run the query manually in Search; note stable text in the winning chunk.
2. Add to `query_eval_cases.json`:

```json
{
  "id": "my-case",
  "query": "your query",
  "mode": "swag",
  "swag": {
    "min_hits": 1,
    "top1_text_any": ["stable phrase from chunk body"]
  }
}
```

For CRAG cases use `"mode": "swag_crag"` and a `"swag_crag"` spec (`forbid_crag_action`, `crag_action`, `max_hits`, etc.).

3. Re-run `./scripts/benchmark-query-battery.sh`.
