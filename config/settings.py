from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API Keys ---
    anthropic_api_key: str = ""
    serpapi_key: str = ""

    # --- Paths ---
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    outputs_dir: Path = BASE_DIR / "outputs"
    raw_html_dir: Path = BASE_DIR / "data" / "raw_html"
    db_path: Path = BASE_DIR / "data" / "groupon_optimizer.duckdb"

    # --- Pipeline tuning ---
    max_concurrency: int = 5      # deals processed in parallel
    scrape_timeout_ms: int = 30000
    max_retries: int = 3
    retry_delay_seconds: float = 2.0

    # --- Rate limiting ---
    requests_per_second: float = 1.0   # for research HTTP calls

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        for d in (self.data_dir, self.outputs_dir, self.raw_html_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
