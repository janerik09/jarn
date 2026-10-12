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
    algorithm: str = "HS256"

    # LDAP
    ldap_enabled: bool = False
    ldap_server: Optional[str] = None
    ldap_port: int = 389
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_password: Optional[str] = None
    ldap_user_search_filter: str = "(&(uid={username})(objectClass=inetOrgPerson))"

    # OAuth2
    oauth2_enabled: bool = False
    oauth2_provider: Optional[str] = None  # okta, azure, google
    oauth2_client_id: Optional[str] = None
    oauth2_client_secret: Optional[str] = None
    oauth2_discovery_url: Optional[str] = None

    # SSH Settings
    ssh_timeout: int = 30
    ssh_port: int = 22
    ssh_key_algorithm: str = "rsa"

    # Job Execution
    max_parallel_jobs: int = 10
    max_retries: int = 3
    retry_delay_seconds: int = 60

    # Patch Scanning
    patch_scan_interval_hours: int = 24

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
