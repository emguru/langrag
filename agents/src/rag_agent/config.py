from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-1"
    bedrock_region: str = "us-east-1"
    default_llm_model: str = "us.amazon.nova-lite-v1:0"
    llm_provider: str = "bedrock"
    openai_api_key: str = ""

    ingestion_api_url: str = "http://localhost:8001"
    ingestion_function_name: str = ""
    rag_top_k: int = 5
    rag_max_tokens: int = 1024
    rag_corrective: bool = True
    hitl_enabled: bool = True

    langsmith_api_key: str = ""
    langsmith_project: str = "rag-demo"

    checkpoint_table: str = "langrag-checkpoints"
    use_dynamodb_checkpointer: bool = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        import os

        if os.getenv("USE_DYNAMODB_CHECKPOINTER", "").lower() in ("1", "true", "yes"):
            self.use_dynamodb_checkpointer = True


settings = Settings()
