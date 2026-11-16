from ingestion.modality import annotate_and_keep, infer_chunk_modality, score_floors


def test_infers_code_markdown_table_and_prose():
    assert infer_chunk_modality({"source": "main.tf", "text": "resource aws"}) == "code"
    assert infer_chunk_modality({"source": "README.md", "text": "hello"}) == "markdown"
    assert (
        infer_chunk_modality(
            {"source": "notes.txt", "text": "| a | b | c |\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |"}
        )
        == "table"
    )
    assert infer_chunk_modality({"source": "essay.txt", "text": "plain paragraph"}) == "prose"


def test_default_floors_are_not_one_number():
    floors = score_floors()
    assert floors["prose"] == 0.12
    assert floors["markdown"] == 0.10
    assert floors["code"] == 0.08
    assert floors["table"] == 0.10
    assert max(floors.values()) - min(floors.values()) >= 0.03


def test_override_forces_one_floor():
    floors = score_floors(override=0.2)
    assert set(floors.values()) == {0.2}


def test_code_survives_below_prose_floor():
    hits = [
        {"id": "md", "source": "guide.md", "text": "intro", "score": 0.11},
        {"id": "py", "source": "app.py", "text": "def main():", "score": 0.09},
        {"id": "weak", "source": "essay.txt", "text": "noise", "score": 0.05},
    ]
    kept = annotate_and_keep(hits, score_floors())
    assert [h["id"] for h in kept] == ["md", "py"]
    assert kept[0]["modality"] == "markdown"
    assert kept[1]["modality"] == "code"
