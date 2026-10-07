import os
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]


def env_file_for(app_env: str) -> str:
    """Env file for an environment: development keeps the plain `.env` used for local work,
    staging/production read `.env.staging` / `.env.production` (copied from their templates).
    `ENV_FILE` overrides the choice. Real environment variables always win over the file —
    on Cloud Run there is no env file at all, only service env vars + Secret Manager."""
    explicit = os.environ.get("ENV_FILE")
    if explicit:
        return explicit
    return ".env" if app_env == "development" else f".env.{app_env}"


# Values copied verbatim from the templates — refusing them outside development
# stops a half-filled env file from reaching GCP.
_PLACEHOLDER_MARKERS = ("your-gcp-project", "change-me", "change_me", "<", "example.com")


class Settings(BaseSettings):
    # Deploy-only keys (DEPLOY_*) share the per-environment file, so unknown keys are ignored.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: AppEnv = "development"
    secret_key: SecretStr

    # Database — on Cloud Run: postgresql+asyncpg://USER:PASS@/DB?host=/cloudsql/PROJECT:REGION:INSTANCE
    database_url: str
    # Per-worker pool. Worst case connections = (pool + overflow) x workers x instances,
    # which must stay below the Cloud SQL instance's max_connections.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Firebase
    firebase_project_id: str = "demo-rusty"
    firebase_auth_emulator_host: str = ""
    firestore_emulator_host: str = ""

    # LLM provider: "ollama" for local dev, "vertex" for Gemini on GCP
    llm_provider: Literal["ollama", "vertex"] = "ollama"

    # Ollama (local dev)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_embed_model: str = "snowflake-arctic-embed2"
    ollama_num_ctx: int = 8192
    ollama_timeout_s: float = 600.0

    # Gemini (LLM_PROVIDER=vertex) — via the google-genai SDK.
    # gemini_auth="adc": Vertex AI / Gemini Enterprise endpoint, authenticated by the Cloud Run
    #   service account (or `gcloud auth application-default login` locally). Recommended.
    # gemini_auth="api_key": Gemini API with GEMINI_API_KEY (inject from Secret Manager, never commit).
    # Default location is the cost path (global endpoint, prompts may be processed outside India).
    # India-resident path: VERTEX_AI_LOCATION=asia-south1, GEMINI_MODEL=gemini-3.5-flash.
    gemini_auth: Literal["adc", "api_key"] = "adc"
    gemini_api_key: SecretStr = SecretStr("")
    vertex_ai_project: str = "demo-rusty"
    vertex_ai_location: str = "global"
    gemini_model: str = "gemini-3.1-flash-lite"
    vertex_embed_model: str = "gemini-embedding-001"

    # Embedding dimension (must match the model used)
    embed_dim: int = 1024

    # GCP infrastructure (unset locally). With TEXTBOOK_BUCKET set, PDF uploads go to Cloud
    # Storage and ingestion runs as the INGEST_JOB_NAME Cloud Run Job instead of in-process.
    gcp_project_id: str = ""
    gcp_region: str = "asia-south1"
    textbook_bucket: str = ""
    ingest_job_name: str = ""

    # Safeguarding
    safeguarding_alert_email: str
    phrases_file_path: str = "./safeguarding/phrases_v1.json"

    # SMTP relay for safeguarding alerts (password from Secret Manager)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""

    # CORS (comma-separated list)
    allowed_origins: str = "http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        if "*" in origins and self.is_production:
            raise ValueError("CORS wildcard '*' is forbidden in production")
        return origins

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def using_emulator(self) -> bool:
        return bool(self.firebase_auth_emulator_host)

    @property
    def uses_cloud_ingestion(self) -> bool:
        return bool(self.textbook_bucket)

    @model_validator(mode="after")
    def _check_cloud_config(self) -> "Settings":
        if self.llm_provider == "vertex" and self.gemini_auth == "api_key" and not self.gemini_api_key.get_secret_value():
            raise ValueError("GEMINI_AUTH=api_key requires GEMINI_API_KEY")
        if self.textbook_bucket and not (self.ingest_job_name and self.gcp_project_id):
            raise ValueError("TEXTBOOK_BUCKET requires INGEST_JOB_NAME and GCP_PROJECT_ID")
        if self.is_development:
            return self

        problems = []
        if self.firebase_auth_emulator_host or self.firestore_emulator_host:
            problems.append("Firebase emulator hosts must be unset")
        if self.llm_provider != "vertex":
            problems.append("LLM_PROVIDER must be 'vertex' (no Ollama on GCP)")
        if "*" in self.allowed_origins:
            problems.append("ALLOWED_ORIGINS must not contain '*'")
        if len(self.secret_key.get_secret_value()) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        checked = {
            "VERTEX_AI_PROJECT": self.vertex_ai_project if self.gemini_auth == "adc" else "",
            "GEMINI_MODEL": self.gemini_model,
            "VERTEX_EMBED_MODEL": self.vertex_embed_model,
            "FIREBASE_PROJECT_ID": self.firebase_project_id,
            "GCP_PROJECT_ID": self.gcp_project_id,
            "TEXTBOOK_BUCKET": self.textbook_bucket,
            "ALLOWED_ORIGINS": self.allowed_origins,
            "SAFEGUARDING_ALERT_EMAIL": self.safeguarding_alert_email,
            "SECRET_KEY": self.secret_key.get_secret_value(),
        }
        for name, value in checked.items():
            lowered = value.lower()
            if any(m in lowered for m in _PLACEHOLDER_MARKERS) or lowered.startswith("demo-"):
                problems.append(f"{name} still holds a template placeholder")
        if problems:
            raise ValueError(f"Invalid {self.app_env} configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    app_env = os.environ.get("APP_ENV", "development")
    return Settings(_env_file=env_file_for(app_env))
