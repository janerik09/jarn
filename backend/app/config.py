from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    app_name: str = "JÁRN"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = "development"

    # Database
    database_url: str = "postgresql://patching_user:patching_password@localhost:5432/patching_db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security
    secret_key: str = "your-secret-key-change-in-production"
    # Browser origins allowed to call the API from another origin, comma separated. Empty (the default) means
    # same-origin only, which is how the bundled nginx serves the dashboard.
    cors_origins: str = ""
    # Optional bearer token for /metrics. When unset, /metrics is only reachable on the internal Docker network.
    metrics_token: Optional[str] = None
    # Base64 of 32 random bytes (AES-256-GCM). Encrypts SSH keys, passwords and backup API tokens
    # stored in the database. Keep it out of the database; losing it makes stored credentials unrecoverable.
    credential_encryption_key: Optional[str] = None
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    # A session ends after this many minutes without a renewal (renewals happen when the user makes a request)
    session_idle_minutes: int = 60
    # A session ends this many hours after sign-in, even if it is in use
    session_max_hours: int = 12
    algorithm: str = "HS256"

    # Logging level for the structured logs
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
