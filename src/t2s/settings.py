from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="T2S_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=13323, ge=1, le=65535)
    mysql_database: str = "text2sql_course"
    mysql_user: str = "course_reader"
    mysql_password: SecretStr = SecretStr("")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:16379/0")
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout: int = Field(default=90, ge=1)
    trace_max_events: int = Field(default=500, ge=1)
    trace_ttl_seconds: int = Field(default=604800, ge=1)
