"""Runtime settings. Values are never logged or echoed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
QDRANT_DIR = DATA_DIR / "qdrant"
ENV_FILE = BACKEND_DIR / ".env"

DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(ENV_FILE, override=False)

# Keep the FastEmbed weights inside the virtualenv, not the project data
# dir and not the OS temp dir. Respects a pre-set FASTEMBED_CACHE_PATH.
if "FASTEMBED_CACHE_PATH" not in os.environ:
    os.environ["FASTEMBED_CACHE_PATH"] = str(Path(sys.prefix) / "fastembed_cache")


class Settings(BaseSettings):
    """Local single-user settings. Secrets stay on the server."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    opik_api_key: str = Field(default="", validation_alias="OPIK_API_KEY")
    opik_workspace: str = Field(default="", validation_alias="OPIK_WORKSPACE")
    opik_project_name: str = Field(default="educator-rag", validation_alias="OPIK_PROJECT_NAME")
    opik_url_override: str = Field(default="", validation_alias="OPIK_URL_OVERRIDE")

    app_host: str = Field(default="127.0.0.1", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")

    reasoning_model: str = "gemini-3.5-flash-lite"
    tts_model: str = "gemini-3.1-flash-tts-preview"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    collection_name: str = "educator_sources"
    chunk_tokens: int = 480
    chunk_overlap: int = 64
    retrieval_limit: int = 6
    max_chunks_per_source: int = 2000
    max_pdf_pages: int = 500

    # Hybrid retrieval: dense (FastEmbed cosine) + async BM25 keyword search,
    # fused with RRF. The fused order is the final ranking; the backend
    # takes top-k (retrieval_limit) chunks from it.
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_candidate_cap: int = 1000
    hybrid_overfetch: int = 20
    hybrid_rrf_k: int = 60
    hybrid_dense_weight: float = 1.0
    hybrid_bm25_weight: float = 1.0

    sqlite_path: Path = DATA_DIR / "educator.db"
    adk_sqlite_path: Path = DATA_DIR / "adk_sessions.db"

    max_upload_bytes: int = 25 * 1024 * 1024
    max_sources_per_query: int = 64
    chat_timeout_s: float = 300.0
    gemini_timeout_s: float = 90.0

    cors_origins: str = Field(default="http://127.0.0.1:5173,http://localhost:5173", validation_alias="CORS_ORIGINS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def log_level_name(self) -> str:
        return (self.log_level or "INFO").strip().upper()

    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip() or os.environ.get("GOOGLE_API_KEY", "").strip())

    @field_validator("chunk_tokens")
    @classmethod
    def _check_chunk_tokens(cls, value: int) -> int:
        if not 64 <= value <= 500:
            raise ValueError("chunk_tokens must be between 64 and 500")
        return value

    @field_validator("retrieval_limit")
    @classmethod
    def _check_retrieval_limit(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("retrieval_limit must be between 1 and 20")
        return value

    @field_validator("bm25_candidate_cap", "hybrid_overfetch", "hybrid_rrf_k")
    @classmethod
    def _check_positive_int(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("hybrid/bm25 caps must be >= 1")
        return int(value)

    @field_validator("bm25_k1", "bm25_b", "hybrid_dense_weight", "hybrid_bm25_weight")
    @classmethod
    def _check_non_negative_float(cls, value: float) -> float:
        if float(value) < 0:
            raise ValueError("bm25/hybrid weights must be >= 0")
        return float(value)

    @field_validator("app_port")
    @classmethod
    def _check_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("APP_PORT must be between 1 and 65535")
        return value

    @model_validator(mode="after")
    def _check_overlap(self) -> Settings:
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_tokens:
            raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_tokens")
        if not 0 <= self.bm25_b <= 1:
            raise ValueError("bm25_b must be between 0 and 1")
        return self

    def opik_configured(self) -> bool:
        return bool(self.opik_api_key.strip() or self.opik_url_override.strip())

    def publish_provider_env(self) -> None:
        """Expose the Gemini key to ADK/google-genai without printing it."""
        key = self.gemini_api_key.strip()
        if key and not os.environ.get("GEMINI_API_KEY"):
            os.environ["GEMINI_API_KEY"] = key
        if key and not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = key
        if self.opik_project_name and not os.environ.get("OPIK_PROJECT_NAME"):
            os.environ["OPIK_PROJECT_NAME"] = self.opik_project_name
        # An empty OPIK_URL_OVERRIDE in backend/.env (the default for cloud)
        # is loaded into os.environ as "". Opik SDK treats a present-but-empty
        # value as an explicit override and builds a relative ping URL, which
        # fails with "missing http:// protocol" and nothing ships. Remove it
        # so the SDK falls back to https://www.comet.com/opik/api/.
        if not self.opik_url_override.strip() and "OPIK_URL_OVERRIDE" in os.environ:
            os.environ.pop("OPIK_URL_OVERRIDE", None)
        if not self.opik_configured():
            # Tracer stays attached. Disable outbound shipping when no backend is set
            # so a missing key does not crash a local study session.
            os.environ.setdefault("OPIK_TRACK_DISABLE", "true")

    def __repr__(self) -> str:  # pragma: no cover - never dump secrets
        return "Settings(redacted)"


settings = Settings()
settings.publish_provider_env()
