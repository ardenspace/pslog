from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", BASE_DIR / ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Database
    database_url: str

    # JWT
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days

    # CORS
    allowed_origins: str = "http://localhost:5173"

    # Crypto (Phase 2 — Fernet 마스터 키)
    pslog_fernet_key: str

    # Phase 5a — webhook callback URL (GitHub 가 호출할 외부 URL, e.g. Cloudflare Tunnel)
    pslog_public_url: str = "http://localhost:8000"

    # Phase 3 — fingerprint 정규화: 절대경로→상대경로 strip 시 prefix
    app_project_root: str = "backend/"


settings = Settings()
