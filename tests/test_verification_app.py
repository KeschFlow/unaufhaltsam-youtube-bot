from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from google.oauth2.credentials import Credentials

from unaufhaltsam_youtube.auth import SCOPES, YOUTUBE_UPLOAD_SCOPE
from unaufhaltsam_youtube.verification_app import PUBLIC_BASE_URL, PUBLIC_REDIRECT_URI


class VerificationAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        secret = root / "web-client.json"
        secret.write_text(json.dumps({"web": {"client_id": "demo.apps.googleusercontent.com", "client_secret": "secret", "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["https://upload.example.com/oauth/callback"]}}), encoding="utf-8")
        self.environment = patch.dict(os.environ, {"VERIFICATION_SESSION_SECRET": "test-session-secret", "YOUTUBE_WEB_CLIENT_SECRET_FILE": str(secret), "YOUTUBE_OAUTH_REDIRECT_URI": "https://upload.example.com/oauth/callback", "VERIFICATION_DB": str(root / "sessions.sqlite3"), "VERIFICATION_MAX_UPLOAD_MB": "25"})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_visible_trigger_and_health_scope(self) -> None:
        from unaufhaltsam_youtube.verification_app import create_app
        client = create_app(testing=True).test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mit YouTube verbinden", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        health = client.get("/healthz").get_json()
        self.assertEqual(health["scope"], YOUTUBE_UPLOAD_SCOPE)
        self.assertEqual(client.get("/health").get_json(), health)
        self.assertEqual(SCOPES, (YOUTUBE_UPLOAD_SCOPE,))
        self.assertEqual(PUBLIC_BASE_URL, "https://upload.eddiesworld.de")
        self.assertEqual(PUBLIC_REDIRECT_URI, "https://upload.eddiesworld.de/oauth/callback")

    def test_oauth_redirect_requests_upload_scope_only(self) -> None:
        from unaufhaltsam_youtube.verification_app import create_app
        response = create_app(testing=True).test_client().get("/oauth/start")
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("youtube.upload", location)
        self.assertNotIn("youtube.force-ssl", location)

    def test_privacy_page_documents_actual_google_data_practices(self) -> None:
        from unaufhaltsam_youtube.verification_app import create_app

        client = create_app(testing=True).test_client()
        self.assertIn(b'href="/privacy"', client.get("/").data)

        response = client.get("/privacy")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("https://www.googleapis.com/auth/youtube.upload", text)
        self.assertIn("weder verkauft noch für Werbung verwendet", text)
        self.assertIn("nicht an Dritte weitergegeben", text)
        self.assertIn("nach spätestens zwei Stunden bereinigt", text)
        self.assertIn("nicht in Anwendungslogs geschrieben", text)
        self.assertIn("https://myaccount.google.com/connections", text)
        self.assertNotIn("youtube.force-ssl", text)

    @patch("unaufhaltsam_youtube.verification_app._load_credentials", return_value=object())
    @patch("unaufhaltsam_youtube.verification_app.upload_video_with_credentials", return_value={"id": "youtube-test-id"})
    def test_selected_mp4_reaches_real_publisher_entrypoint(self, upload_mock, _credentials_mock) -> None:
        from unaufhaltsam_youtube.verification_app import create_app
        client = create_app(testing=True).test_client()
        with client.session_transaction() as browser_session:
            browser_session["session_id"] = "test-session"
            browser_session["csrf"] = "test-csrf"
        response = client.post(
            "/upload",
            data={
                "csrf": "test-csrf",
                "title": "UNAUFHALTSAM Test",
                "video": (BytesIO(b"\x00\x00\x00\x18ftypmp42valid-test-video-bytes"), "test.mp4"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"youtube-test-id", response.data)
        upload_mock.assert_called_once()
        metadata = upload_mock.call_args.args[0]
        self.assertNotIn("score", metadata)
        self.assertNotIn("human_record", metadata)
        self.assertEqual(metadata["privacy_status"], "private")

    def test_complete_oauth_callback_form_and_publisher_flow_without_external_calls(self) -> None:
        from unaufhaltsam_youtube.verification_app import create_app

        credentials = Credentials(
            token="local-test-token",
            refresh_token="local-test-refresh",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="demo.apps.googleusercontent.com",
            client_secret="secret",
            scopes=SCOPES,
        )

        class FakeFlow:
            def __init__(self):
                self.credentials = credentials
                self.code_verifier = None

            def authorization_url(self, **_kwargs):
                self.code_verifier = "fixed-pkce-code-verifier"
                return "https://accounts.google.com/o/oauth2/auth?scope=youtube.upload", "fixed-state"

            def fetch_token(self, **_kwargs):
                return None

        fake_flow = FakeFlow()
        with patch("unaufhaltsam_youtube.verification_app.Flow.from_client_secrets_file", return_value=fake_flow) as flow_factory, patch(
            "unaufhaltsam_youtube.verification_app.upload_video_with_credentials",
            return_value={"id": "youtube-integration-test-id"},
        ) as upload_mock:
            client = create_app(testing=True).test_client()
            landing = client.get("/")
            self.assertIn(b"Mit YouTube verbinden", landing.data)
            oauth = client.get("/oauth/start")
            self.assertEqual(oauth.status_code, 302)
            callback = client.get("/oauth/callback?state=fixed-state&code=local-code")
            self.assertEqual(callback.status_code, 200)
            self.assertIn("Video auswählen".encode(), callback.data)
            self.assertEqual(flow_factory.call_args_list[1].kwargs["code_verifier"], "fixed-pkce-code-verifier")
            with client.session_transaction() as browser_session:
                csrf = browser_session["csrf"]
                self.assertNotIn("oauth_code_verifier", browser_session)
            result = client.post(
                "/upload",
                data={
                    "csrf": csrf,
                    "title": "UNAUFHALTSAM Integrationstest",
                    "description": "Lokaler Test ohne externen API-Aufruf.",
                    "video": (BytesIO(b"\x00\x00\x00\x18ftypmp42integration-test"), "integration.mp4"),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(result.status_code, 200)
            self.assertIn(b"youtube-integration-test-id", result.data)
            upload_mock.assert_called_once()

    def test_non_mp4_content_is_rejected_before_publisher(self) -> None:
        from unaufhaltsam_youtube.verification_app import create_app

        client = create_app(testing=True).test_client()
        with client.session_transaction() as browser_session:
            browser_session["session_id"] = "test-session"
            browser_session["csrf"] = "test-csrf"
        with patch("unaufhaltsam_youtube.verification_app._load_credentials", return_value=object()), patch(
            "unaufhaltsam_youtube.verification_app.upload_video_with_credentials"
        ) as upload_mock:
            response = client.post(
                "/upload",
                data={
                    "csrf": "test-csrf",
                    "title": "UNAUFHALTSAM Test",
                    "video": (BytesIO(b"not-an-mp4"), "fake.mp4"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        upload_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
