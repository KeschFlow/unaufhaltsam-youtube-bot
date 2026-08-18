from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials

from unaufhaltsam_youtube.auth import (
    SCOPES,
    YOUTUBE_UPLOAD_SCOPE,
    OAuthConfigurationError,
    validate_client_secret,
    validate_credentials_scope,
)


class OAuthConfigurationTests(unittest.TestCase):
    def test_scope_is_upload_only(self) -> None:
        self.assertEqual(SCOPES, (YOUTUBE_UPLOAD_SCOPE,))

    def test_project_has_no_other_active_google_youtube_scope(self) -> None:
        project = Path(__file__).resolve().parent.parent
        source_files = [
            *project.glob("*.py"),
            *project.glob("*.json"),
            *project.glob("*.toml"),
            *project.glob("*.env"),
            *project.glob("unaufhaltsam_youtube/*.py"),
        ]
        scope_prefix = "https://www.googleapis.com/auth/"
        found: set[str] = set()
        for path in source_files:
            text = path.read_text(encoding="utf-8")
            for token in text.replace('"', " ").replace("'", " ").split():
                if token.startswith(scope_prefix):
                    found.add(token.rstrip(",);"))
        self.assertEqual(found, {YOUTUBE_UPLOAD_SCOPE})

    def test_missing_secret_is_rejected(self) -> None:
        with self.assertRaises(OAuthConfigurationError):
            validate_client_secret(Path("definitely-missing-client-secret.json"))

    def test_desktop_client_is_accepted(self) -> None:
        payload = {
            "installed": {
                "client_id": "test.apps.googleusercontent.com",
                "client_secret": "test-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client_secret.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            validate_client_secret(path)

    def test_web_client_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client_secret.json"
            path.write_text(json.dumps({"web": {}}), encoding="utf-8")
            with self.assertRaises(OAuthConfigurationError):
                validate_client_secret(path)

    def test_token_with_additional_scope_is_rejected(self) -> None:
        credentials = Credentials(
            token="test", refresh_token="refresh", token_uri="https://oauth2.googleapis.com/token",
            client_id="client", client_secret="secret",
            scopes=[YOUTUBE_UPLOAD_SCOPE, "https://www.googleapis.com/auth/youtube.readonly"],
            expiry=datetime.now(UTC) + timedelta(hours=1),
        )
        with self.assertRaises(OAuthConfigurationError):
            validate_credentials_scope(credentials)


if __name__ == "__main__":
    unittest.main()
