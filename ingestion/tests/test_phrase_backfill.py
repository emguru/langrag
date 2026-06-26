from ingestion.crag import hits_for_crag_grading
from ingestion.rag_store import merge_phrase_candidates


def test_merge_phrase_candidates_prepends_and_boosts_score():
    vector = [
        {"id": "a", "score": 0.18, "text": "terraform docs"},
        {"id": "b", "score": 0.15, "text": "other"},
    ]
    phrase = [
        {"id": "c", "score": 0.0, "text": "outside that training scope", "phrase_match": True},
    ]
    merged = merge_phrase_candidates(vector, phrase)
    assert merged[0]["id"] == "c"
    assert merged[0]["score"] > 0.18
    assert merged[0]["phrase_match"] is True
    assert [h["id"] for h in merged] == ["c", "a", "b"]


def test_merge_phrase_candidates_dedupes_vector_overlap():
    vector = [{"id": "a", "score": 0.2, "text": "training scope notes"}]
    phrase = [{"id": "a", "score": 0.0, "text": "training scope notes", "phrase_match": True}]
    merged = merge_phrase_candidates(vector, phrase)
    assert len(merged) == 1
    assert merged[0]["phrase_match"] is True


def test_hits_for_crag_grading_includes_phrase_matches_beyond_limit():
    hits = [
        {"id": "1", "phrase_match": True},
        {"id": "2"},
        {"id": "3"},
        {"id": "4"},
        {"id": "5"},
        {"id": "6"},
    ]
    graded = hits_for_crag_grading(hits, grade_limit=3)
    assert graded[0]["id"] == "1"
    assert len(graded) == 4
    assert {h["id"] for h in graded} == {"1", "2", "3", "4"}