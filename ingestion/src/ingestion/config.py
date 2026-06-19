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


settings = Settings()
