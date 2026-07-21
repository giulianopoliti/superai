from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="Stock AI", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")


settings = Settings()
