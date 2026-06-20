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
    max_upload_bytes: int = 100 * 1024 * 1024
    max_fetch_bytes: int = 100 * 1024 * 1024
    rate_limit_ingest_per_min: int = 30
    rate_limit_search_per_min: int = 30

    # Corrective RAG (CRAG)
    crag_enabled: bool = True
    crag_grader_model: str = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    crag_retrieve_multiplier: int = 2
    crag_relevant_score: float = 0.55
    crag_ambiguous_score: float = 0.35
    crag_grade_chunk_chars: int = 600


settings = Settings()
