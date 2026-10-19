# File purpose: loads safe project settings from the root .env and environment variables.
"""Environment-backed project configuration."""
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# Step 1: load only the private root .env file; the template is never treated as a secret.
def _load_project_environment() -> None:
    """Load only the project's ignored root .env file; never read the template as secrets."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


# Step 2: report key presence as a Boolean without returning its value.
def api_key_is_configured() -> bool:
    """Return presence only; callers must never display the key itself."""
    _load_project_environment()
    return bool(os.getenv("OPENFDA_API_KEY", "").strip())


# Step 3: store parsed settings in an immutable record.
@dataclass(frozen=True)
class Settings:
    api_key: str | None
    start_date: date
    end_date: date
    timeout_seconds: float
    max_retries: int
    sample_size: int


# Step 4: parse optional values and provide validated defaults for API calls.
def get_settings() -> Settings:
    """Load optional .env values and validate configuration defaults."""
    _load_project_environment()
    end_text = os.getenv("END_DATE", "").strip()
    return Settings(
        api_key=os.getenv("OPENFDA_API_KEY") or None,
        start_date=date.fromisoformat(os.getenv("START_DATE", "2019-01-01")),
        end_date=date.fromisoformat(end_text) if end_text else date.today(),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", "20")),
        max_retries=max(0, int(os.getenv("API_MAX_RETRIES", "3"))),
        sample_size=max(1, int(os.getenv("SAMPLE_SIZE", "100"))),
    )


if __name__ == "__main__":
    print(f"OPENFDA_API_KEY configured: {'yes' if api_key_is_configured() else 'no'}")
