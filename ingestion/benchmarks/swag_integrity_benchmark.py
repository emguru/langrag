#!/usr/bin/env python3
"""Compare legacy vs hardened SWAG scoring on labeled adversarial + clean cases."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.legacy_swag_scoring import score_swag_alignment as legacy_score
from ingestion.config import settings
from ingestion.swag.schemas import SWAGProfile
from ingestion.swag.scoring import score_swag_alignment as hardened_score

VECTOR_W = settings.swag_vector_weight
SWAG_W = settings.swag_weight
META_W = settings.swag_metadata_weight
TOP_K = 2


def precision_at_1(ranked_ids: list[str], relevant: set[str]) -> float:
    if not ranked_ids:
        return 0.0
    return 1.0 if ranked_ids[0] in relevant else 0.0


@dataclass
class Chunk:
    id: str
    text: str
    vector_score: float
    profile: SWAGProfile


@dataclass
class Case:
    name: str
    query: SWAGProfile
    chunks: list[Chunk]
    relevant_ids: set[str]
    adversarial_ids: set[str] = field(default_factory=set)


def final_score(vector: float, swag: float, metadata: float = 0.0) -> float:
    return vector * VECTOR_W + swag * SWAG_W + metadata * META_W


def rerank(
    case: Case,
    score_fn,
) -> list[tuple[str, float, float, float]]:
    ranked: list[tuple[str, float, float, float]] = []
    for chunk in case.chunks:
        swag, _ = score_fn(case.query, chunk.profile, chunk_text=chunk.text)
        total = final_score(chunk.vector_score, swag)
        ranked.append((chunk.id, chunk.vector_score, swag, total))
    ranked.sort(key=lambda row: row[3], reverse=True)
    return ranked


def precision_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    hits = sum(1 for cid in ranked_ids[:k] if cid in relevant)
    return hits / k


def recall_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = sum(1 for cid in ranked_ids[:k] if cid in relevant)
    return hits / len(relevant)


def mrr(ranked_ids: list[str], relevant: set[str]) -> float:
    for idx, cid in enumerate(ranked_ids, start=1):
        if cid in relevant:
            return 1.0 / idx
    return 0.0


def adversarial_in_top_k(ranked_ids: list[str], adversarial: set[str], k: int) -> float:
    if not adversarial:
        return 0.0
    return sum(1 for cid in ranked_ids[:k] if cid in adversarial) / k


def build_cases() -> list[Case]:
    return [
        Case(
            name="short_substring_trap",
            query=SWAGProfile(
                primary_concepts=["AI"],
                detected_domain="technology",
                confidence=0.9,
            ),
            chunks=[
                Chunk(
                    id="relevant_ai",
                    text="Our AI governance policy defines model approval workflows.",
                    vector_score=0.62,
                    profile=SWAGProfile(
                        primary_concepts=["AI governance"],
                        detected_domain="technology",
                        confidence=0.9,
                    ),
                ),
                Chunk(
                    id="trap_mail",
                    text="Configure SMTP mail relay for outbound email delivery.",
                    vector_score=0.64,
                    profile=SWAGProfile(
                        primary_concepts=["mail"],
                        domain_terms=["email"],
                        detected_domain="technology",
                        confidence=0.85,
                    ),
                ),
            ],
            relevant_ids={"relevant_ai"},
            adversarial_ids={"trap_mail"},
        ),
        Case(
            name="query_negative_context",
            query=SWAGProfile(
                primary_concepts=["Apple"],
                negative_contexts=["fruit", "orchard"],
                detected_domain="technology",
                confidence=0.9,
            ),
            chunks=[
                Chunk(
                    id="relevant_apple_inc",
                    text="Apple silicon MacBooks use unified memory architecture.",
                    vector_score=0.60,
                    profile=SWAGProfile(
                        primary_concepts=["Apple silicon"],
                        detected_domain="technology",
                        confidence=0.9,
                    ),
                ),
                Chunk(
                    id="trap_fruit",
                    text="Apple orchard harvest schedules and fruit storage guidelines.",
                    vector_score=0.63,
                    profile=SWAGProfile(
                        primary_concepts=["apple orchard", "fruit"],
                        detected_domain="agriculture",
                        confidence=0.9,
                    ),
                ),
            ],
            relevant_ids={"relevant_apple_inc"},
            adversarial_ids={"trap_fruit"},
        ),
        Case(
            name="ungrounded_profile_term",
            query=SWAGProfile(
                primary_concepts=["blockchain"],
                detected_domain="finance",
                confidence=0.9,
            ),
            chunks=[
                Chunk(
                    id="relevant_invoice",
                    text="Three-way invoice matching validates PO, receipt, and invoice totals.",
                    vector_score=0.58,
                    profile=SWAGProfile(
                        primary_concepts=["invoice matching"],
                        detected_domain="finance",
                        confidence=0.9,
                    ),
                ),
                Chunk(
                    id="trap_hallucinated",
                    text="Three-way invoice matching validates PO, receipt, and invoice totals.",
                    vector_score=0.61,
                    profile=SWAGProfile(
                        primary_concepts=["blockchain", "distributed ledger"],
                        detected_domain="finance",
                        confidence=0.95,
                    ),
                ),
            ],
            relevant_ids={"relevant_invoice"},
            adversarial_ids={"trap_hallucinated"},
        ),
        Case(
            name="clean_invoice_match",
            query=SWAGProfile(
                primary_concepts=["vendor invoice matching"],
                user_intents=["how does invoice matching work"],
                detected_domain="finance",
                confidence=0.9,
            ),
            chunks=[
                Chunk(
                    id="best",
                    text="Vendor invoice matching compares supplier invoices to purchase orders.",
                    vector_score=0.70,
                    profile=SWAGProfile(
                        primary_concepts=["vendor invoice matching"],
                        domain_terms=["purchase orders"],
                        detected_domain="finance",
                        confidence=0.95,
                    ),
                ),
                Chunk(
                    id="weak",
                    text="Employee expense reimbursement uses receipt photos uploaded by staff.",
                    vector_score=0.55,
                    profile=SWAGProfile(
                        primary_concepts=["expense reimbursement"],
                        detected_domain="finance",
                        confidence=0.8,
                    ),
                ),
            ],
            relevant_ids={"best"},
            adversarial_ids=set(),
        ),
    ]


def evaluate(label: str, score_fn, cases: list[Case]) -> dict:
    rows = []
    totals = {
        "precision_at_1": 0.0,
        "precision_at_k": 0.0,
        "recall_at_k": 0.0,
        "mrr": 0.0,
        "adversarial_rate_at_k": 0.0,
        "adversarial_swag_mean": 0.0,
    }
    for case in cases:
        ranked = rerank(case, score_fn)
        ranked_ids = [row[0] for row in ranked]
        adv_swag = [
            row[2]
            for row in ranked
            if row[0] in case.adversarial_ids
        ]
        adv_swag_mean = sum(adv_swag) / len(adv_swag) if adv_swag else 0.0
        row = {
            "case": case.name,
            "top_k": ranked_ids[:TOP_K],
            "precision_at_1": precision_at_1(ranked_ids, case.relevant_ids),
            "precision_at_k": precision_at_k(ranked_ids, case.relevant_ids, TOP_K),
            "recall_at_k": recall_at_k(ranked_ids, case.relevant_ids, TOP_K),
            "mrr": mrr(ranked_ids, case.relevant_ids),
            "adversarial_rate_at_k": adversarial_in_top_k(
                ranked_ids, case.adversarial_ids, TOP_K
            ),
            "adversarial_swag_mean": round(adv_swag_mean, 4),
            "ranking": [
                {"id": cid, "vector": round(v, 3), "swag": round(s, 3), "final": round(f, 3)}
                for cid, v, s, f in ranked
            ],
        }
        rows.append(row)
        for key in totals:
            totals[key] += row[key]

    count = len(cases)
    summary = {key: round(value / count, 4) for key, value in totals.items()}
    return {"label": label, "summary": summary, "cases": rows}


def delta(hardened: dict, legacy: dict) -> dict:
    out = {}
    for key in hardened["summary"]:
        h = hardened["summary"][key]
        l = legacy["summary"][key]
        out[key] = round(h - l, 4)
        if key in {"adversarial_rate_at_k", "adversarial_swag_mean"}:
            out[f"{key}_direction"] = "lower_is_better"
        else:
            out[f"{key}_direction"] = "higher_is_better"
    return out


def main() -> int:
    cases = build_cases()
    legacy = evaluate("legacy", legacy_score, cases)
    hardened = evaluate("hardened", hardened_score, cases)
    changes = delta(hardened, legacy)

    report = {
        "top_k": TOP_K,
        "weights": {"vector": VECTOR_W, "swag": SWAG_W, "metadata": META_W},
        "legacy": legacy["summary"],
        "hardened": hardened["summary"],
        "delta_hardened_minus_legacy": changes,
        "cases": {
            "legacy": legacy["cases"],
            "hardened": hardened["cases"],
        },
    }
    print(json.dumps(report, indent=2))

    improved_recall = changes["recall_at_k"] >= 0
    reduced_adversarial = (
        changes["adversarial_rate_at_k"] <= 0 and changes["adversarial_swag_mean"] <= 0
    )
    print("\n--- Interpretation ---")
    print(
        f"Precision@1: {legacy['summary']['precision_at_1']:.2%} → "
        f"{hardened['summary']['precision_at_1']:.2%} "
        f"({changes['precision_at_1']:+.2%})"
    )
    print(
        f"Precision@{TOP_K}: {legacy['summary']['precision_at_k']:.2%} → "
        f"{hardened['summary']['precision_at_k']:.2%} "
        f"({changes['precision_at_k']:+.2%})"
    )
    print(
        f"Recall@{TOP_K}: {legacy['summary']['recall_at_k']:.2%} → "
        f"{hardened['summary']['recall_at_k']:.2%} "
        f"({changes['recall_at_k']:+.2%})"
    )
    print(
        f"MRR: {legacy['summary']['mrr']:.3f} → {hardened['summary']['mrr']:.3f} "
        f"({changes['mrr']:+.3f})"
    )
    print(
        f"Adversarial in top-{TOP_K}: {legacy['summary']['adversarial_rate_at_k']:.2%} → "
        f"{hardened['summary']['adversarial_rate_at_k']:.2%} "
        f"({changes['adversarial_rate_at_k']:+.2%}, lower is better)"
    )
    print(
        f"Mean adversarial SWAG score: {legacy['summary']['adversarial_swag_mean']:.3f} → "
        f"{hardened['summary']['adversarial_swag_mean']:.3f} "
        f"({changes['adversarial_swag_mean']:+.3f}, lower is better)"
    )
    if reduced_adversarial and changes["precision_at_k"] >= 0:
        print("Net: integrity fixes improved or held precision while reducing adversarial hits.")
    elif reduced_adversarial and changes["recall_at_k"] < 0:
        print("Net: fewer false positives, some recall tradeoff on edge cases.")
    else:
        print("Net: mixed movement — inspect per-case rankings below.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
