from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    secret_key: str

    # Database
    database_url: str

    # Firebase
    firebase_project_id: str = "demo-rusty"
    firebase_auth_emulator_host: str = ""
    firestore_emulator_host: str = ""

    # LLM provider: "ollama" for local dev, "vertex" for production
    llm_provider: Literal["ollama", "vertex"] = "ollama"

    # Ollama (local dev)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_embed_model: str = "snowflake-arctic-embed2"

    # GCP / Vertex AI (production) — via the google-genai SDK.
    # Default is the cost path (global endpoint, prompts may be processed outside India).
    # India-resident path: VERTEX_AI_LOCATION=asia-south1, GEMINI_MODEL=gemini-3.5-flash.
    vertex_ai_project: str = "demo-rusty"
    vertex_ai_location: str = "global"
    gemini_model: str = "gemini-3.1-flash-lite"
    vertex_embed_model: str = "gemini-embedding-001"

    # Embedding dimension (must match the model used)
    embed_dim: int = 1024

    # Safeguarding
    safeguarding_alert_email: str
    phrases_file_path: str = "./safeguarding/phrases_v1.json"

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
    def using_emulator(self) -> bool:
        return bool(self.firebase_auth_emulator_host)


@lru_cache
def get_settings() -> Settings:
    return Settings()
