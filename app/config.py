import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "University Visual Profile API")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    environment: str = os.getenv("APP_ENV", "development")
    debug: bool = _get_bool("APP_DEBUG")
    wikidata_api_url: str = os.getenv(
        "WIKIDATA_API_URL", "https://www.wikidata.org/w/api.php"
    )
    wikidata_timeout_seconds: float = float(
        os.getenv("WIKIDATA_TIMEOUT_SECONDS", "8.0")
    )
    wikidata_search_limit: int = int(os.getenv("WIKIDATA_SEARCH_LIMIT", "10"))
    wikidata_maxlag_seconds: int = int(os.getenv("WIKIDATA_MAXLAG_SECONDS", "10"))
    wikidata_user_agent: str = os.getenv(
        "WIKIDATA_USER_AGENT",
        "UniversityVisualProfileMVP/0.1 (hackathon backend; contact: local-development)",
    )
    wikimedia_commons_api_url: str = os.getenv(
        "WIKIMEDIA_COMMONS_API_URL", "https://commons.wikimedia.org/w/api.php"
    )
    wikimedia_timeout_seconds: float = float(
        os.getenv("WIKIMEDIA_TIMEOUT_SECONDS", "10.0")
    )
    wikimedia_maxlag_seconds: int = int(
        os.getenv("WIKIMEDIA_MAXLAG_SECONDS", "10")
    )
    wikimedia_retry_attempts: int = int(
        os.getenv("WIKIMEDIA_RETRY_ATTEMPTS", "3")
    )
    image_download_timeout_seconds: float = float(
        os.getenv("IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "10.0")
    )
    image_download_max_bytes: int = int(
        os.getenv("IMAGE_DOWNLOAD_MAX_BYTES", "15000000")
    )
    perceptual_hash_threshold: int = int(
        os.getenv("PERCEPTUAL_HASH_THRESHOLD", "2")
    )
    ai_provider: str = os.getenv("AI_PROVIDER", "gemini")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    ai_timeout_seconds: float = float(os.getenv("AI_TIMEOUT_SECONDS", "30.0"))
    ai_retry_attempts: int = int(os.getenv("AI_RETRY_ATTEMPTS", "3"))
    ai_requests_per_minute: int = int(os.getenv("AI_REQUESTS_PER_MINUTE", "30"))
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    profile_timeout_seconds: float = float(
        os.getenv("PROFILE_TIMEOUT_SECONDS", "120.0")
    )
    profile_cache_ttl_seconds: float = float(
        os.getenv("PROFILE_CACHE_TTL_SECONDS", "900.0")
    )
    profile_limit_per_query: int = int(
        os.getenv("PROFILE_LIMIT_PER_QUERY", "2")
    )
    profile_ai_concurrency: int = int(
        os.getenv("PROFILE_AI_CONCURRENCY", "2")
    )


settings = Settings()
