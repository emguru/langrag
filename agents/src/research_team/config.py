from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LangSmith
    langsmith_api_key: str = ""
    langsmith_project: str = "research-showcase"
    langsmith_tracing: bool = True

    # LLM
    bedrock_region: str = "us-east-1"
    default_llm_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    default_embedding_model: str = "amazon.titan-embed-text-v2:0"
    openai_api_key: str = ""
    llm_provider: str = "bedrock"  # bedrock | openai

    # RAG
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "research_docs"
    rag_top_k: int = 8
    ingestion_api_url: str = "http://localhost:8001"

    # Research tuning
    max_researcher_parallel: int = 2
    max_iterations: int = 2

    # External search (optional)
    tavily_api_key: str = ""


settings = Settings()
