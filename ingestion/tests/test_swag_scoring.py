from ingestion.swag.schemas import SemanticCategory, SWAGProfile, WeightedSemanticTerm
from ingestion.swag.scoring import score_swag_alignment
from ingestion.swag.utils import fuzzy_term_match, term_grounded_in_text
from ingestion.swag.validation import sanitize_profile_data


def test_fuzzy_rejects_short_substring_match():
    assert not fuzzy_term_match("ai", "mail")
    assert not fuzzy_term_match("it", "kubernetes")
    assert fuzzy_term_match("apple", "apple")


def test_query_negative_excluded_from_positive_matching():
    query = SWAGProfile(
        primary_concepts=["invoice"],
        negative_contexts=["fruit"],
        weighted_terms=[
            WeightedSemanticTerm(
                term="fruit",
                category=SemanticCategory.NEGATIVE_CONTEXT,
                weight=1.0,
                reason="avoid",
            )
        ],
    )
    chunk = SWAGProfile(
        primary_concepts=["fruit"],
        domain_terms=["orchard"],
    )
    score, explanations = score_swag_alignment(
        query,
        chunk,
        chunk_text="The orchard ships fresh fruit weekly.",
    )
    assert score < 0.5
    assert any("query negative context" in e for e in explanations)


def test_ungrounded_chunk_term_discounted():
    query = SWAGProfile(primary_concepts=["quantum computing"])
    chunk = SWAGProfile(primary_concepts=["quantum computing"])
    grounded_score, _ = score_swag_alignment(
        query,
        chunk,
        chunk_text="This document discusses quantum computing research.",
    )
    ungrounded_score, ungrounded_expl = score_swag_alignment(
        query,
        chunk,
        chunk_text="Completely unrelated content about payroll.",
    )
    assert grounded_score > ungrounded_score
    assert any("ungrounded" in e for e in ungrounded_expl)


def test_sanitize_caps_profile_size():
    data = {
        "detected_domain": "x" * 200,
        "primary_concepts": [f"term-{i}" for i in range(50)],
        "weighted_terms": [
            {"term": f"w-{i}", "category": "primary_concept", "weight": 1.0, "reason": "r"}
            for i in range(50)
        ],
    }
    sanitized = sanitize_profile_data(data)
    assert len(sanitized["primary_concepts"]) <= 12
    assert len(sanitized["weighted_terms"]) <= 24
    assert len(sanitized["detected_domain"]) <= 64


def test_term_grounding_requires_token_overlap():
    assert term_grounded_in_text("invoice matching", "Vendor invoice matching rules apply.")
    assert not term_grounded_in_text("blockchain", "Vendor invoice matching rules apply.")
