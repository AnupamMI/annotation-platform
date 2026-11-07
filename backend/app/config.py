from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or the project-root .env file."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://annot:annot_dev_password@localhost:5432/annot"
    redis_url: str = "redis://localhost:6379/0"

    # "s3" = MinIO from docker compose (default); "local" = a folder on disk
    storage_backend: str = "s3"
    local_storage_dir: str = "../data/storage"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "annot"
    s3_secret_key: str = "annot_dev_password"
    s3_bucket: str = "annot-data"

    max_upload_mb: int = 200  # ordinary images
    max_slide_mb: int = 10240  # whole-slide files (.svs, .ndpi, ...), streamed to disk
    slide_cache_dir: str = ""  # where slides are kept for tiling; default: the system temp folder
    max_import_mb: int = 2048
    # Images wider or taller than this are cut into a Deep Zoom tile pyramid
    tile_threshold_px: int = 4096
    tile_size: int = 254

    # Pluggable LLM provider: "anthropic", "openai", "ollama" or "none"
    llm_provider: str = "none"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""  # optional: OpenAI-compatible server, or Ollama on another machine
    llm_timeout_s: float = 60

    # Ask (question -> read-only SQL)
    ask_timeout_ms: int = 5000
    ask_row_limit: int = 200

    # Login
    cookie_secure: bool = False  # set true when served over HTTPS

    # Dev convenience: create tables on startup. Replaced by Alembic migrations later.
    create_tables_on_startup: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
