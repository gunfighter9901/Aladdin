"""Runtime settings. Values are never logged or echoed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
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

    reasoning_model: str = "gemini-3.1-flash-lite"
    tts_model: str = "gemini-3.1-flash-tts-preview"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    collection_name: str = "educator_sources"
    chunk_tokens: int = 480
    chunk_overlap: int = 64
    retrieval_limit: int = 6

    sqlite_path: Path = DATA_DIR / "educator.db"
    adk_sqlite_path: Path = DATA_DIR / "adk_sessions.db"

    max_upload_bytes: int = 25 * 1024 * 1024

    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip() or os.environ.get("GOOGLE_API_KEY", "").strip())

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
