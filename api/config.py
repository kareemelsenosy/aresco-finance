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
