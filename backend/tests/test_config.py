"""Tests for per-environment config selection and the non-development safety checks."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings, env_file_for

_CLOUD = dict(
    app_env="staging",
    secret_key="s" * 64,
    database_url="postgresql+asyncpg://u:p@/rusty?host=/cloudsql/p:asia-south1:rusty-db",
    llm_provider="vertex",
    vertex_ai_project="rusty-staging-khel",
    gemini_model="gemini-3.1-flash-lite",
    vertex_embed_model="gemini-embedding-001",
    firebase_project_id="rusty-staging-khel",
    gcp_project_id="rusty-staging-khel",
    textbook_bucket="rusty-staging-khel-textbooks",
    ingest_job_name="rusty-ingest",
    safeguarding_alert_email="safeguarding@khel.org",
    allowed_origins="https://rusty-staging-khel.web.app",
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_CLOUD, **overrides})


def test_env_file_per_environment(monkeypatch):
    monkeypatch.delenv("ENV_FILE", raising=False)
    assert env_file_for("development") == ".env"
    assert env_file_for("staging") == ".env.staging"
    assert env_file_for("production") == ".env.production"


def test_env_file_override(monkeypatch):
    monkeypatch.setenv("ENV_FILE", "/secrets/rusty.env")
    assert env_file_for("production") == "/secrets/rusty.env"


def test_valid_cloud_config_passes():
    s = _settings()
    assert s.uses_cloud_ingestion
    assert not s.is_development


def test_development_allows_local_defaults():
    s = Settings(
        _env_file=None, app_env="development", secret_key="x", database_url="postgresql+asyncpg://localhost/db",
        safeguarding_alert_email="admin@example.com", firebase_auth_emulator_host="localhost:9099",
    )
    assert s.llm_provider == "ollama"
    assert not s.uses_cloud_ingestion


@pytest.mark.parametrize("override, message", [
    ({"vertex_ai_project": "<PROJECT_ID>"}, "VERTEX_AI_PROJECT"),
    ({"gemini_model": "<GEMINI_MODEL e.g. gemini-3.1-flash-lite>"}, "GEMINI_MODEL"),
    ({"firebase_project_id": "demo-rusty"}, "FIREBASE_PROJECT_ID"),
    ({"firebase_auth_emulator_host": "localhost:9099"}, "emulator"),
    ({"llm_provider": "ollama"}, "LLM_PROVIDER"),
    ({"allowed_origins": "*"}, "ALLOWED_ORIGINS"),
    ({"secret_key": "short"}, "SECRET_KEY"),
])
def test_cloud_config_rejects_unsafe_values(override, message):
    with pytest.raises(ValidationError, match=message):
        _settings(**override)


def test_api_key_auth_requires_key():
    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        _settings(gemini_auth="api_key")
    assert _settings(gemini_auth="api_key", gemini_api_key="k").gemini_auth == "api_key"


def test_bucket_requires_job_and_project():
    with pytest.raises(ValidationError, match="INGEST_JOB_NAME"):
        _settings(ingest_job_name="")


def test_secrets_are_masked_in_repr():
    s = _settings(gemini_auth="api_key", gemini_api_key="super-secret-key")
    assert "super-secret-key" not in repr(s)
    assert "s" * 64 not in repr(s)
