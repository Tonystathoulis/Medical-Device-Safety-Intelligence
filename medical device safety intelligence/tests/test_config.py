# File purpose: verifies default settings and confirms key checks reveal presence only.
from datetime import date
from medical_device_safety.config import api_key_is_configured, get_settings

# Clear configuration values, load settings, and confirm safe defaults are applied.
def test_defaults_without_api_key(monkeypatch):
    for key in ("OPENFDA_API_KEY", "START_DATE", "END_DATE", "API_TIMEOUT_SECONDS", "API_MAX_RETRIES", "SAMPLE_SIZE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENFDA_API_KEY", "")
    settings = get_settings()
    assert settings.api_key is None
    assert settings.start_date == date(2019, 1, 1)
    assert settings.end_date == date.today()
    assert settings.sample_size == 100


# Supply and then blank a fake key, checking only the Boolean presence result.
def test_safe_api_key_check_reports_presence_only(monkeypatch):
    monkeypatch.setenv("OPENFDA_API_KEY", "fake-test-key")
    assert api_key_is_configured() is True
    monkeypatch.setenv("OPENFDA_API_KEY", " ")
    assert api_key_is_configured() is False
