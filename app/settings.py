from decimal import Decimal
from pathlib import Path
from typing import Literal

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
    web_auth_enabled: bool = Field(default=False, validation_alias="WEB_AUTH_ENABLED")
    supabase_url: str | None = Field(default=None, validation_alias="SUPABASE_URL")
    aws_origin_secret: str | None = Field(default=None, validation_alias="AWS_ORIGIN_SECRET")
    web_cors_origins: list[str] = Field(default_factory=list, validation_alias="WEB_CORS_ORIGINS")
    jobs_enabled: bool = Field(default=False, validation_alias="JOBS_ENABLED")
    job_files_dir: Path = Field(default=Path(".data/jobs"), validation_alias="JOB_FILES_DIR")
    job_lease_seconds: int = Field(default=120, ge=30, validation_alias="JOB_LEASE_SECONDS")
    job_heartbeat_seconds: int = Field(default=20, ge=1, validation_alias="JOB_HEARTBEAT_SECONDS")
    job_retention_days: int = Field(default=30, ge=1, validation_alias="JOB_RETENTION_DAYS")
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
    openai_document_model: str = Field(
        default="gpt-6-luna", validation_alias="OPENAI_DOCUMENT_MODEL",
    )
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
        default=True, validation_alias="DOCUMENT_ANALYSIS_V2_ENABLED",
    )
    offer_decisions_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("OFFER_IDENTITY_ENABLED", "OFFER_DECISIONS_ENABLED"),
    )
    offer_identity_provider: Literal["jev", "openai_decisions"] = Field(
        default="jev", validation_alias="OFFER_IDENTITY_PROVIDER",
    )
    offer_decisions_max_calls: int = Field(
        default=6, ge=1, le=20, validation_alias="OFFER_DECISIONS_MAX_CALLS",
    )
    offer_decisions_max_spend_usd: Decimal = Field(
        default=Decimal("0.02"), gt=0, le=1, validation_alias="OFFER_DECISIONS_MAX_SPEND_USD",
    )
    document_analysis_max_calls: int = Field(
        default=100, ge=1, validation_alias="DOCUMENT_ANALYSIS_MAX_CALLS",
        description="Maximum reader calls per document continuation (at most two continuations).",
    )
    document_analysis_timeout_seconds: float = Field(
        default=180, gt=0, validation_alias="DOCUMENT_ANALYSIS_TIMEOUT_SECONDS",
        description="V2 processing time budget per backend batch.",
    )
    document_analysis_max_metadata_bytes: int = Field(
        default=4_000_000, ge=1024, validation_alias="DOCUMENT_ANALYSIS_MAX_METADATA_BYTES",
    )


settings = Settings()
