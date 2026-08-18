"""Central local paths."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = PROJECT_ROOT / "secrets"
OUTPUT_DIR = PROJECT_ROOT / "output"
RECORDINGS_DIR = OUTPUT_DIR / "recordings"
VIDEOS_DIR = OUTPUT_DIR / "videos"
RESULTS_DIR = OUTPUT_DIR / "results"
METADATA_DIR = OUTPUT_DIR / "metadata"
LOGS_DIR = OUTPUT_DIR / "logs"
DB_FILE = OUTPUT_DIR / "autopilot.sqlite3"


def client_secret_file() -> Path:
    """Return the OAuth client file, allowing an explicit local override."""
    configured = os.environ.get("YOUTUBE_CLIENT_SECRET_FILE")
    return Path(configured).expanduser().resolve() if configured else SECRETS_DIR / "client_secret.json"


def token_file() -> Path:
    """Return the local user-token file, allowing an explicit local override."""
    configured = os.environ.get("YOUTUBE_TOKEN_FILE")
    return Path(configured).expanduser().resolve() if configured else SECRETS_DIR / "token.json"
