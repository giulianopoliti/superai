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


settings = Settings()
