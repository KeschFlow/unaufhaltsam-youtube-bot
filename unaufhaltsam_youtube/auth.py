"""Interactive OAuth authorization for YouTube uploads.

This module obtains and refreshes credentials only. It contains no video upload
or publication call.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .paths import client_secret_file, token_file


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SCOPES = (YOUTUBE_UPLOAD_SCOPE,)


class OAuthConfigurationError(RuntimeError):
    """Raised when local OAuth configuration is missing or malformed."""


def validate_credentials_scope(credentials: Credentials) -> None:
    """Reject tokens that do not contain exactly the upload-only scope."""
    granted = set(credentials.scopes or ())
    if granted != set(SCOPES):
        raise OAuthConfigurationError(
            "OAuth-Token muss ausschliesslich youtube.upload enthalten; "
            "bitte neu autorisieren."
        )


def validate_client_secret(path: Path) -> None:
    """Validate that *path* is a Google OAuth desktop-client JSON file."""
    if not path.is_file():
        raise OAuthConfigurationError(
            f"OAuth-Datei fehlt: {path}\n"
            "Lege den heruntergeladenen Desktop-Client dort als client_secret.json ab."
        )

    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OAuthConfigurationError(f"OAuth-Datei ist keine gueltige JSON-Datei: {path}") from exc

    installed = payload.get("installed")
    required = {"client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris"}
    if not isinstance(installed, dict) or not required.issubset(installed):
        raise OAuthConfigurationError(
            "OAuth-Datei ist kein vollstaendiger Google OAuth-Desktop-Client."
        )


def _save_token(credentials: Credentials, path: Path) -> None:
    """Atomically store credentials in the ignored secrets directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=".token-", suffix=".tmp", text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(credentials.to_json())
            handle.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def authorize() -> Credentials:
    """Load, refresh, or interactively create upload-only credentials."""
    secret_path = client_secret_file()
    stored_token_path = token_file()
    validate_client_secret(secret_path)

    credentials: Credentials | None = None
    if stored_token_path.is_file():
        credentials = Credentials.from_authorized_user_file(stored_token_path, SCOPES)
        validate_credentials_scope(credentials)

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            authorization_prompt_message="Browser-Anmeldung wird geoeffnet: {url}",
            success_message=(
                "Google-Freigabe abgeschlossen. Dieses Browserfenster kann geschlossen werden."
            ),
            access_type="offline",
            prompt="consent",
        )

    validate_credentials_scope(credentials)
    _save_token(credentials, stored_token_path)
    return credentials


def check_configuration() -> None:
    """Validate local prerequisites without network access or browser login."""
    secret_path = client_secret_file()
    validate_client_secret(secret_path)
    print(f"OAuth-Desktop-Client ist gueltig: {secret_path}")
    print(f"Angeforderter Scope: {YOUTUBE_UPLOAD_SCOPE}")
    print("Es wurde weder eine Google-Anmeldung noch ein Upload gestartet.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Lokale OAuth-Konfiguration offline pruefen")
    subparsers.add_parser("authorize", help="Manuelle Google-OAuth-Freigabe starten")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            check_configuration()
        elif args.command == "authorize":
            credentials = authorize()
            print("OAuth erfolgreich; lokales Token wurde sicher gespeichert.")
            print(f"Erteilte Scopes: {', '.join(credentials.scopes or SCOPES)}")
            print("Es wurde kein Video hochgeladen oder veroeffentlicht.")
    except OAuthConfigurationError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"OAuth fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
