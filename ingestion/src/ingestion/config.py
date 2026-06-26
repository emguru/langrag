from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-1"
    s3_document_bucket: str = "local-documents"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "research_docs"

    bedrock_region: str = "us-east-1"
    default_embedding_model: str = "amazon.titan-embed-text-v2:0"
    openai_api_key: str = ""
    llm_provider: str = "bedrock"

    chunk_size: int = 1000
    chunk_overlap: int = 200
    batch_size: int = 32
    rag_top_k: int = 8
    # Minimum top vector similarity to return hits (0 = disabled). Blocks off-corpus junk.
    rag_min_vector_score: float = 0.12
    # Lower floor when a topic metadata filter narrows the corpus (scoped lane search).
    rag_min_vector_score_topic: float = 0.08
    # When a chunk passes filtering, include same-document siblings from the retrieval pool.
    rag_expand_same_document: bool = False
    rag_same_document_max_per_doc: int = 3
    # Pad results from the vector pool when fewer than top_k survive filtering.
    rag_pool_backfill: bool = False
    # Inject chunks whose text contains the full query phrase into the vector pool
    # (recall fix when embeddings miss an exact phrase match).
    rag_phrase_candidate_backfill: bool = True
    rag_phrase_max_hits: int = 3
    rag_phrase_min_words: int = 2
    # Drop title/nav/boilerplate from search hits; keeps body chunks only when possible.
    rag_filter_non_body_hits: bool = True
    # Compare baseline uses same expand+prune stack as augmented hits (honest eval).
    rag_compare_symmetric_baseline: bool = True
    max_upload_bytes: int = 100 * 1024 * 1024
    max_fetch_bytes: int = 100 * 1024 * 1024
    rate_limit_ingest_per_min: int = 30
    rate_limit_search_per_min: int = 30

    # Corrective RAG (CRAG)
    crag_enabled: bool = True
    crag_grader_model: str = "us.anthropic.claude-3-haiku-20240307-v1:0"
    crag_retrieve_multiplier: int = 2
    crag_relevant_score: float = 0.55
    crag_ambiguous_score: float = 0.35
    crag_grade_chunk_chars: int = 600
    # Max chunks sent to the Haiku grader per request (cost control).
    crag_grade_max_chunks: int = 5
    # When the grader marks every chunk irrelevant, keep chunks with lexical/fuzzy
    # query overlap (e.g. typo "pancackes" vs indexed "pancakes").
    crag_lexical_rescue: bool = True
    crag_lexical_fuzzy_ratio: float = 0.84

    # SWAG (Semantic Weighted Augmented Generation)
    swag_enabled: bool = True
    swag_ingest_profiles: bool = True
    # llm = Bedrock grader; heuristic = rule-based profiles (tests / low-cost demo)
    swag_profile_mode: str = "heuristic"
    swag_grader_model: str = "us.anthropic.claude-sonnet-4-6"
    swag_retrieval_candidates: int = 20
    swag_vector_weight: float = 0.65
    swag_weight: float = 0.30
    swag_metadata_weight: float = 0.05
    swag_min_score_threshold: float = 0.08
    swag_noise_vector_margin: float = 0.04
    # Without SWAG floor, vector must be within this ratio of the top hit.
    swag_noise_relative_vector_ratio: float = 0.78
    # Second-pass ratio when strict gate returns fewer than top_k hits.
    swag_noise_backfill_ratio: float = 0.58
    swag_noise_backfill_enabled: bool = False
    swag_evaluative_blend_boost: float = 0.15
    swag_evaluative_title_penalty: float = 0.65
    swag_chunk_title_multiplier: float = 0.40
    swag_chunk_nav_multiplier: float = 0.30
    swag_chunk_boilerplate_multiplier: float = 0.02
    swag_intent_category_boost: float = 1.25
    swag_max_chunks_per_source: int = 1
    swag_deduplicate_similarity: float = 0.92
    swag_adaptive_blend: bool = True
    swag_adaptive_vector_epsilon: float = 0.03
    swag_adaptive_swag_boost: float = 0.12
    swag_persist_profiles: bool = True
    swag_max_terms_per_list: int = 12
    swag_max_weighted_terms: int = 24
    swag_max_term_chars: int = 80
    swag_max_reason_chars: int = 120
    swag_max_domain_chars: int = 64
    swag_ungrounded_term_weight_multiplier: float = 0.35
    swag_grounding_min_token_ratio: float = 0.5
    swag_fuzzy_min_token_len: int = 4
    swag_gated_rerank: bool = True
    structural_split: bool = True


settings = Settings()
