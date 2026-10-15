"""Fetch embedding assets once after `pip install -r requirements.txt`.

Weights go into the virtualenv's FastEmbed cache (`$VIRTUAL_ENV/fastembed_cache`,
via FASTEMBED_CACHE_PATH set in app.config). Tokenizer uses the HuggingFace Hub
cache. Run once:

    cd backend
    python scripts/download_model.py

Re-running is safe: assets already in the cache are detected and skipped.
Use `--force` to re-download anyway.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings


def _supported_entry(model_name: str) -> dict | None:
    from fastembed import TextEmbedding

    for entry in TextEmbedding.list_supported_models():
        if entry.get("model") == model_name:
            return entry
    return None


def _fastembed_cache_dir() -> Path:
    from fastembed.common.utils import define_cache_dir

    return define_cache_dir()


def _embedding_cached(model_name: str) -> bool:
    """True when the ONNX snapshot is already in the FastEmbed cache."""
    entry = _supported_entry(model_name)
    if not entry:
        return False
    sources = entry.get("sources") or {}
    hf_repo = sources.get("hf")
    model_file = entry.get("model_file") or "model_optimized.onnx"
    if not hf_repo:
        return False
    cache_repo = "models--" + hf_repo.replace("/", "--")
    snapshots = _fastembed_cache_dir() / cache_repo / "snapshots"
    if not snapshots.is_dir():
        return False
    return any((snap / model_file).is_file() for snap in snapshots.iterdir() if snap.is_dir())


def _tokenizer_cached(model_name: str) -> bool:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import LocalEntryNotFoundError

    try:
        hf_hub_download(model_name, "tokenizer.json", local_files_only=True)
        return True
    except LocalEntryNotFoundError:
        return False
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Download embedding assets if missing.")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached.")
    args = parser.parse_args()

    from fastembed import TextEmbedding
    from huggingface_hub import hf_hub_download

    model_name = settings.embedding_model
    force = args.force

    if not force and _embedding_cached(model_name):
        print(f"Embedding model cached, skipping: {model_name}")
    else:
        print(f"Downloading embedding model: {model_name}")
        embedder = TextEmbedding(model_name=model_name)
        list(embedder.embed(["embedding warmup"]))
        print("Embedding model ready.")

    if not force and _tokenizer_cached(model_name):
        print(f"Tokenizer cached, skipping: {model_name} tokenizer.json")
    else:
        print(f"Downloading tokenizer: {model_name} tokenizer.json")
        hf_hub_download(model_name, "tokenizer.json")
        print("Tokenizer ready.")


if __name__ == "__main__":
    main()
