import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
import query_eval


def _hit(text: str, *, kind: str | None = None) -> dict:
    payload = {"text": text}
    if kind:
        payload["chunk_kind"] = kind
    return payload


def test_score_case_passes_body_top1_and_beats_title():
    case = {
        "id": "demo",
        "query": "what is rag",
        "swag": {
            "top1_kind": "body",
            "require_beats_baseline_title": True,
        },
    }
    baseline = [_hit("# RAG From Scratch", kind="title")]
    swag = [_hit("LLMs are trained on a large but fixed corpus of data.", kind="body")]
    result = query_eval.score_case(case, swag_hits=swag, baseline_hits=baseline, top_k=3)
    assert result["passed"] is True


def test_score_case_fails_when_title_stays_on_top():
    case = {
        "id": "demo",
        "query": "what is rag",
        "swag": {
            "top1_kind": "body",
            "require_beats_baseline_title": True,
        },
    }
    baseline = [_hit("# RAG From Scratch", kind="title")]
    swag = [_hit("# RAG From Scratch", kind="title")]
    result = query_eval.score_case(case, swag_hits=swag, baseline_hits=baseline, top_k=3)
    assert result["passed"] is False


def test_summarize_eval_threshold():
    results = [{"passed": True}, {"passed": False}]
    summary = query_eval.summarize_eval(results, threshold=0.8)
    assert summary["verdict"] == "FAIL"
    assert summary["passed"] == 1


def test_summarize_eval_excludes_skipped():
    results = [
        {"passed": True},
        {"passed": True},
        {"skipped": True, "passed": False},
    ]
    summary = query_eval.summarize_eval(results, threshold=0.8)
    assert summary["verdict"] == "PASS"
    assert summary["total"] == 2
    assert summary["skipped"] == 1


def test_score_crag_case_partial_rag():
    case = {
        "id": "cross",
        "query": "how do we automate rag",
        "swag_crag": {
            "min_hits": 1,
            "forbid_crag_action": "web_fallback",
            "top1_text_any": ["retrieval augmented generation"],
        },
    }
    hits = [_hit("Retrieval augmented generation combines search with LLMs.", kind="body")]
    crag = {"action": "partial_rag", "kept": 1}
    result = query_eval.score_case(
        case,
        swag_hits=hits,
        baseline_hits=[],
        top_k=8,
        crag_meta=crag,
        spec_key="swag_crag",
    )
    assert result["passed"] is True


def test_score_crag_case_web_fallback_expected():
    case = {
        "id": "noise",
        "query": "invoice cafeteria menu",
        "swag_crag": {"max_hits": 0, "crag_action": "web_fallback"},
    }
    result = query_eval.score_case(
        case,
        swag_hits=[],
        baseline_hits=[],
        top_k=8,
        crag_meta={"action": "web_fallback", "kept": 0},
        spec_key="swag_crag",
    )
    assert result["passed"] is True
