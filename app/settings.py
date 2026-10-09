from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Stock AI", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    default_business_id: str = Field(
        default="demo-business",
        validation_alias="DEFAULT_BUSINESS_ID",
    )
    kapso_api_key: str | None = Field(default=None, validation_alias="KAPSO_API_KEY")
    kapso_webhook_secret: str | None = Field(default=None, validation_alias="KAPSO_WEBHOOK_SECRET")
    kapso_sandbox_phone_number_id: str | None = Field(
        default=None,
        validation_alias="KAPSO_SANDBOX_PHONE_NUMBER_ID",
    )
    internal_api_token: str | None = Field(default=None, validation_alias="INTERNAL_API_TOKEN")
    scheduler_enabled: bool = Field(default=False, validation_alias="SCHEDULER_ENABLED")
    scheduler_interval_seconds: int = Field(
        default=60,
        validation_alias="SCHEDULER_INTERVAL_SECONDS",
    )
    llm_enabled: bool = Field(default=False, validation_alias="LLM_ENABLED")
    agent_enabled: bool = Field(default=False, validation_alias="AGENT_ENABLED")
    agent_minimum_confidence: float = Field(
        default=0.70, validation_alias="AGENT_MINIMUM_CONFIDENCE"
    )
    agent_max_steps: int = Field(default=5, validation_alias="AGENT_MAX_STEPS")
    llm_provider: str = Field(default="openai", validation_alias="LLM_PROVIDER")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.6-flash", validation_alias="GEMINI_MODEL")
    gemini_document_model: str | None = Field(
        default="gemini-flash-latest",
        validation_alias="GEMINI_DOCUMENT_MODEL",
    )
    google_vision_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_VISION_API_KEY", "GOOGLE_CLOUD_VISION_API_KEY"),
    )
    google_oauth_client_id: str | None = Field(
        default=None,
        validation_alias="GOOGLE_OAUTH_CLIENT_ID",
    )
    google_oauth_client_secret: str | None = Field(
        default=None,
        validation_alias="GOOGLE_OAUTH_CLIENT_SECRET",
    )
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.6-luna", validation_alias="OPENAI_MODEL")
    typesafe_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TYPESAFE_AI_API_KEY", "JEV_API_KEY"),
    )
    llm_timeout_seconds: float = Field(default=6.0, validation_alias="LLM_TIMEOUT_SECONDS")
    document_extraction_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="DOCUMENT_EXTRACTION_TIMEOUT_SECONDS",
    )
    document_extraction_chunk_size: int = Field(
        default=2,
        ge=1,
        le=6,
        validation_alias="DOCUMENT_EXTRACTION_CHUNK_SIZE",
    )
    document_extraction_chunk_workers: int = Field(
        default=2,
        ge=1,
        le=2,
        validation_alias="DOCUMENT_EXTRACTION_CHUNK_WORKERS",
    )
    document_extraction_retry_workers: int = Field(
        default=2,
        ge=1,
        le=2,
        validation_alias="DOCUMENT_EXTRACTION_RETRY_WORKERS",
    )
    document_extraction_retry_low_coverage: bool = Field(
        default=True,
        validation_alias="DOCUMENT_EXTRACTION_RETRY_LOW_COVERAGE",
    )
    jev_reconciliation_model: str = Field(
        default="jev-1.13.0",
        validation_alias="JEV_RECONCILIATION_MODEL",
    )
    document_analysis_v2_enabled: bool = Field(
        default=False, validation_alias="DOCUMENT_ANALYSIS_V2_ENABLED",
    )
    document_analysis_max_calls: int = Field(
        default=100, ge=1, validation_alias="DOCUMENT_ANALYSIS_MAX_CALLS",
        description="Maximum calls per V2 backend batch, not per PDF.",
    )
    document_analysis_timeout_seconds: float = Field(
        default=180, gt=0, validation_alias="DOCUMENT_ANALYSIS_TIMEOUT_SECONDS",
        description="V2 processing time budget per backend batch.",
    )
    document_analysis_max_metadata_bytes: int = Field(
        default=4_000_000, ge=1024, validation_alias="DOCUMENT_ANALYSIS_MAX_METADATA_BYTES",
    )


settings = Settings()
