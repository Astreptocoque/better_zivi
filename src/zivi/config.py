"""Paths and settings, overridable with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ZIVI_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = Path(os.environ.get("ZIVI_DB", DATA_DIR / "zivi.sqlite"))
GEONAMES_DIR = DATA_DIR / "geonames"

# Set ZIVI_OFFLINE=1 to never call online geocoders (only the GeoNames postcode table).
OFFLINE = os.environ.get("ZIVI_OFFLINE", "").lower() in {"1", "true", "yes"}

# Optional AI (embeddings) settings. Any OpenAI-compatible /embeddings endpoint works:
# OpenAI, Mistral, Azure OpenAI (compatible mode), a local Ollama (http://localhost:11434/v1), ...
AI_BASE_URL = os.environ.get("ZIVI_AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL = os.environ.get("ZIVI_AI_MODEL", "text-embedding-3-small")
AI_API_KEY = os.environ.get("ZIVI_AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""

USER_AGENT = "better-zivi/0.1 (personal search tool)"
