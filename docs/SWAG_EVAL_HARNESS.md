# SWAG Evaluation Harness

Serious, testable retrieval evaluation across **five ugly corpora** and **strong baselines**. This harness is additive — it does **not** replace the existing tuning suite (`validation_qrels.json`, `run-validation-offline.sh`).

## What it proves

Enterprise RAG needs **retrieval quality**, **grounding**, **explainability**, and tolerance for **real document messiness** — not just final-answer vibes.

The harness measures whether SWAG is a **corpus understanding layer** (synonym/acronym/version/conflict awareness) or only a demo reranker.

## Corpora (5 domains)

Fixtures live under `fixtures/corpus/eval-harness/`:

| Corpus | Topic tag | Messiness |
|--------|-----------|-----------|
| `retail-furniture` | `eval-retail` | Deprecated sizing, duplicates, partial assembly extract |
| `terraform-cloud` | `eval-terraform` | Version traps (1.2 vs 1.5), acronyms (VPC/IAM/IRSA) |
| `legal-contracts` | `eval-legal` | Archived MSA v1, cross-doc checklist/DPA |
| `medical-admin` | `eval-medical` | HIPAA/PHI/BAA, outdated 2019 infection policy |
| `mixed-garbage` | `eval-garbage` | Contradictions, duplicates, partial PDFs, acronym soup |

Each corpus has a manifest + qrels under `ingestion/benchmarks/eval_harness/corpora/<id>/`.

## Benchmark scale

**Target:** 100 questions per corpus (40 direct, 20 synonym, 20 acronym, 10 version trap, 10 cross-doc).

**Pilot (shipped):** 20 questions per corpus (100 total) with full qrel fields:

- `question`, `expected_answer`, `gold_source_document`, `gold_source_chunk_contains`
- `acceptable_alternate_terms`, `trap_documents`, `forbidden_top1_seed_keys`
- `category`: `direct` | `synonym` | `acronym` | `version_trap` | `cross_doc`

Expand to 100 by editing `ingestion/benchmarks/eval_harness/generate_pilot_qrels.py` and re-running:

```bash
cd ingestion && .venv/bin/python benchmarks/eval_harness/generate_pilot_qrels.py
```

## Baselines (strong, not toy)

| Method | Description |
|--------|-------------|
| `bm25` | Okapi BM25 over chunk text |
| `vector` | Embedding cosine (same embedder as production) |
| `hybrid_rrf` | Reciprocal rank fusion of BM25 + vector |
| `hybrid_rerank` | Vector pool + BM25 re-order (lexical reranker) |
| `swag` | Vector pool + SWAG rerank/filter pipeline |

Future: dedicated cross-encoder reranker, alternate embedding model — add to `eval_harness/baselines.py` without touching production search.

## Metrics

Per case / aggregated:

- Recall@5, Precision@5, MRR, nDCG@5
- Citation correctness@1 (gold doc + chunk marker)
- Wrong-version rate (trap docs in top-5)
- Latency (ms per method)

Answer correctness / hallucination rate: enable with `--generate` (extractive by default; `--generate-mode llm` for Bedrock/OpenAI).

```bash
./scripts/run-eval-harness.sh --generate
./scripts/run-eval-harness.sh --generate --generate-mode llm --generate-method swag
```

With `--generate`, the harness scores all four pillars per qrel:

| Pillar | Scored |
|--------|--------|
| Input corpus | manifest + topic filter |
| Input question | `query` field |
| Expected retrieval | recall@5, citation@1, wrong-version rate |
| Expected answer | `expected_answer`, `acceptable_alternate_terms`, grounded + hallucination checks |

Answer thresholds live in `eval_harness/manifest.json` under `answer_thresholds`.

## Brutal thresholds

Configured in `eval_harness/manifest.json`:

- **+10%** recall@5 vs `hybrid_rrf` on **≥4 of 5** corpora
- **+15%** on hard categories: synonym, acronym, version_trap
- SWAG wrong-version rate ≤ hybrid
- SWAG latency ≤ **3×** vector-only

Exit code **1** when thresholds fail — intentional for CI honesty.

## Run locally

```bash
./scripts/run-eval-harness.sh
```

Options:

```bash
./scripts/run-eval-harness.sh --corpus retail-furniture --json-only
./scripts/run-eval-harness.sh --corpus terraform-cloud --pool-k 30
```

Environment:

- `SWAG_PROFILE_MODE=heuristic` (default in script — no Bedrock grader cost)
- Requires embedding access (Bedrock Titan or OpenAI per `ingestion` settings)

Results JSON written to `ingestion/benchmarks/eval_harness/results/` (gitignored).

## Architecture

```
fixtures/corpus/eval-harness/     # ugly docs
        ↓
eval_harness/index_builder.py   # chunk + embed + BM25 index (offline)
        ↓
eval_harness/baselines.py         # bm25 | vector | hybrid_* | swag
        ↓
eval_harness/metrics.py           # recall, ndcg, citation, wrong-version
        ↓
eval_harness/thresholds.py        # product-ready signal
        ↓
eval_harness/run.py               # CLI orchestrator
```

Existing validation (`validation_eval.py`, `run-validation-offline.sh`) remains the **regression gate** for kube demo behavior. This harness is the **product proof** gate.

## Current pilot signal (example)

Early pilot runs typically show hybrid retrieval strong on lexical overlap while SWAG must earn wins on **version traps** and **chrome/noise** — if SWAG loses to `hybrid_rrf` globally, thresholds correctly report **NOT PRODUCT-WORTHY YET**.

That is the point: falsifiable benchmarks before marketing SWAG as a product.
