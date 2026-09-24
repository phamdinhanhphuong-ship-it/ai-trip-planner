from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    cache_dir: str = ".cache"

    groq_api_key: str = ""
    tomtom_api_key: str = ""
    mapbox_api_key: str = ""
    geoapify_api_key: str = ""
    goong_api_key: str = ""

    # Nominatim requires a descriptive User-Agent per its usage policy.
    nominatim_user_agent: str = "guidepass-ai-test/0.1"


settings = Settings()
