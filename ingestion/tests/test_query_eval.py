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
