from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    llm_provider: str = Field(default="gemini", validation_alias="LLM_PROVIDER")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.6-flash", validation_alias="GEMINI_MODEL")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.6-luna", validation_alias="OPENAI_MODEL")
    llm_timeout_seconds: float = Field(default=6.0, validation_alias="LLM_TIMEOUT_SECONDS")


settings = Settings()
