from unittest.mock import patch

from ingestion.huggingface_ingest import resolve_config_split

WIKI_SPLITS = [
    {"config": "text-corpus", "split": "passages"},
    {"config": "question-answer", "split": "train"},
]

SQUAD_SPLITS = [
    {"config": "plain_text", "split": "train"},
    {"config": "plain_text", "split": "validation"},
]


def test_exact_match_kept():
    with patch("ingestion.huggingface_ingest.list_splits", return_value=WIKI_SPLITS):
        config, split, _ = resolve_config_split(
            "rag-datasets/rag-mini-wikipedia", "text-corpus", "passages"
        )
    assert (config, split) == ("text-corpus", "passages")


def test_leftover_preset_config_is_ignored():
    """Custom dataset must not fail because the UI still has the previous preset's config."""
    with patch("ingestion.huggingface_ingest.list_splits", return_value=SQUAD_SPLITS):
        config, split, _ = resolve_config_split("rajpurkar/squad", "text-corpus", "passages")
    assert (config, split) == ("plain_text", "train")


def test_generic_default_train_is_a_hint():
    only_passages = [{"config": "text-corpus", "split": "passages"}]
    with patch("ingestion.huggingface_ingest.list_splits", return_value=only_passages):
        config, split, _ = resolve_config_split(
            "rag-datasets/rag-mini-wikipedia", "default", "train"
        )
    assert (config, split) == ("text-corpus", "passages")


def test_matching_split_on_other_config_is_kept():
    with patch("ingestion.huggingface_ingest.list_splits", return_value=SQUAD_SPLITS):
        config, split, _ = resolve_config_split("rajpurkar/squad", "missing-config", "validation")
    assert (config, split) == ("plain_text", "validation")


def test_known_config_wrong_split_stays_on_config():
    with patch("ingestion.huggingface_ingest.list_splits", return_value=SQUAD_SPLITS):
        config, split, _ = resolve_config_split("rajpurkar/squad", "plain_text", "passages")
    assert config == "plain_text"
    assert split in {"train", "validation"}
