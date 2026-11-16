from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-1"
    s3_document_bucket: str = "local-documents"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "research_docs"

    bedrock_region: str = "us-east-1"
    default_embedding_model: str = "amazon.titan-embed-text-v2:0"
    openai_api_key: str = ""
    llm_provider: str = "bedrock"

    chunk_size: int = 1000
    chunk_overlap: int = 200
    batch_size: int = 32
    rag_top_k: int = 8
    rag_min_vector_score: float = 0.12
    rag_min_vector_score_topic: float = 0.08
    rag_min_vector_score_prose: float = 0.12
    rag_min_vector_score_markdown: float = 0.10
    rag_min_vector_score_code: float = 0.08
    rag_min_vector_score_table: float = 0.10
    rag_hybrid: bool = True
    rag_rerank: bool = True
    rerank_model_id: str = "amazon.rerank-v1:0"
    rerank_region: str = "us-west-2"
    rag_phrase_candidate_backfill: bool = True
    rag_phrase_min_words: int = 2
    rag_phrase_max_hits: int = 8

    max_upload_bytes: int = 100 * 1024 * 1024
    max_fetch_bytes: int = 100 * 1024 * 1024
    rate_limit_ingest_per_min: int = 30
    rate_limit_search_per_min: int = 30

    swag_profile_mode: str = "heuristic"


settings = Settings()
