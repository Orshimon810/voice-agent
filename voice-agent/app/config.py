from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    airtable_token: str
    airtable_base_id: str = "app5XBvVamnrsToQQ"
    airtable_table_id: str = "tblr7Qkcp3dg55JWQ"

    vapi_api_key: str
    vapi_assistant_id: str
    vapi_phone_number_id: str

    webhook_secret: str


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
