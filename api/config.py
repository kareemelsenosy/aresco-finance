from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # --- LLM provider (audit agent) ---
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # --- App ---
    database_url: str = "sqlite:///./aresco_finance.db"
    upload_dir: str = "./uploads"
    max_upload_mb: int = 200
    # Signs session cookies and verification codes. Set a real value in .env —
    # changing it invalidates every existing session.
    secret_key: str = "change-me"

    # --- Access control ---
    app_display_name: str = "ARESCO Treasury & Finance"
    # Only addresses at this domain may register or sign in.
    allowed_email_domain: str = "aresco.com.eg"
    session_hours: int = 12
    code_ttl_minutes: int = 15

    # --- Outbound mail (verification codes) ---
    # With no smtp_host the code is written to the server log instead.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    # --- Reporting defaults ---
    base_currency: str = "EGP"
    # Days before a scheduled outflow triggers a shortage warning
    shortage_buffer_egp: float = 0.0

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
