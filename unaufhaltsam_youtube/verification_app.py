"""Public Google-verification flow for a user-initiated YouTube upload."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, redirect, render_template_string, request, session, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from .auth import SCOPES, YOUTUBE_UPLOAD_SCOPE
from .publisher import upload_video_with_credentials

PUBLIC_BASE_URL = "https://upload.eddiesworld.de"
PUBLIC_REDIRECT_URI = f"{PUBLIC_BASE_URL}/oauth/callback"
DEFAULT_MAX_UPLOAD_MB = 25
MAX_CONFIGURED_UPLOAD_MB = 512
SESSION_TTL = timedelta(hours=2)
STATE_TTL = timedelta(minutes=10)
LOGGER = logging.getLogger(__name__)

PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>UNAUFHALTSAM – YouTube-Upload</title>
<style>body{font-family:system-ui;background:#08080d;color:#fff;max-width:760px;margin:4rem auto;padding:1.5rem}.card{border:1px solid #7430a8;border-radius:18px;padding:2rem;background:#111118}button,a.button{display:inline-block;background:#8b32c9;color:white;border:0;border-radius:10px;padding:.9rem 1.2rem;text-decoration:none;font-weight:700;cursor:pointer}input,textarea{display:block;box-sizing:border-box;margin:.5rem 0 1rem;padding:.8rem;width:100%;max-width:100%}.muted{color:#bbb}.error{color:#ffb4b4}</style></head><body><main class="card">
<h1>UNAUFHALTSAM → YouTube</h1>
<p>Diese Funktion lädt ein von dir ausgewähltes UNAUFHALTSAM-Video auf deinen eigenen YouTube-Kanal.</p>
<p class="muted">Google-Zugriff: ausschließlich <code>youtube.upload</code>. Der Upload wird privat angelegt.</p>
{% if step == 'start' %}<a class="button" href="{{ url_for('oauth_start') }}">Mit YouTube verbinden</a>
{% elif step == 'upload' %}<h2>Video auswählen</h2><form method="post" enctype="multipart/form-data" action="{{ url_for('upload') }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><label>MP4-Video (max. {{ max_upload_mb }} MB)<input required type="file" name="video" accept="video/mp4,.mp4"></label>
<label>Titel<input required maxlength="100" name="title" value="UNAUFHALTSAM – Gameplay"></label>
<label>Beschreibung<textarea maxlength="5000" name="description">Vom Nutzer ausgewählter UNAUFHALTSAM-Gameplay-Upload.</textarea></label>
<button type="submit">Privat auf meinen YouTube-Kanal hochladen</button></form>
{% elif step == 'done' %}<h2>Upload abgeschlossen</h2><p>Die YouTube-Video-ID wurde empfangen: <code>{{ video_id }}</code></p>
{% else %}<h2>Upload fehlgeschlagen</h2><p class="error">Der Upload wurde nicht veröffentlicht. Bitte verbinde YouTube erneut und versuche es noch einmal.</p>{% endif %}
<p style="margin-top:2rem"><a href="{{ url_for('privacy') }}">Datenschutzrichtlinie</a></p>
</main></body></html>"""

PRIVACY_PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Datenschutzrichtlinie – UNAUFHALTSAM YouTube-Upload</title>
<style>body{font-family:system-ui;background:#08080d;color:#fff;max-width:820px;margin:3rem auto;padding:1.5rem;line-height:1.6}.card{border:1px solid #7430a8;border-radius:18px;padding:2rem;background:#111118}h1,h2{line-height:1.2}h2{margin-top:2rem}a{color:#d9a8ff}code{overflow-wrap:anywhere}.muted{color:#bbb}</style></head><body><main class="card">
<h1>Datenschutzrichtlinie</h1>
<p class="muted">Für die UNAUFHALTSAM-YouTube-Upload-Funktion</p>

<h2>Google-Autorisierung und Zweck</h2>
<p>UNAUFHALTSAM greift nur nach deiner ausdrücklichen Autorisierung über Google OAuth auf deinen YouTube-Kanal zu. Die Anwendung fordert ausschließlich den Scope <code>https://www.googleapis.com/auth/youtube.upload</code> an. Dieser Zugriff wird nur verwendet, um eine von dir ausgewählte Videodatei auf deinen eigenen YouTube-Kanal hochzuladen. Die Anwendung erhält dadurch keinen Zugriff auf dein Google-Passwort.</p>

<h2>Verwendete Google-Nutzerdaten</h2>
<p>Für den von dir gestarteten Upload verarbeitet die Anwendung OAuth-Zugangs- und Aktualisierungstokens. Sie übermittelt die von dir ausgewählte Videodatei sowie den von dir eingegebenen Titel und die Beschreibung an die YouTube API. Die von YouTube nach einem erfolgreichen Upload zurückgegebene Video-ID wird dir auf der Ergebnisseite angezeigt.</p>
<p>Google-Nutzerdaten werden weder verkauft noch für Werbung verwendet.</p>

<h2>Weitergabe und Offenlegung</h2>
<p>Google-Nutzerdaten werden nicht an Dritte weitergegeben, übertragen oder offengelegt. Ausnahmen bestehen nur, soweit die Übertragung an Google beziehungsweise die YouTube API technisch erforderlich ist, um den von dir ausdrücklich angeforderten Upload auszuführen, oder soweit eine gesetzliche Verpflichtung zur Offenlegung besteht.</p>

<h2>Speicherung und Löschung</h2>
<p>OAuth-Tokens werden serverseitig in einer nicht öffentlich zugänglichen Sitzungsdatenbank gespeichert. Sie werden nach Abschluss oder Fehlschlagen des Uploads gelöscht; abgelaufene Sitzungen werden nach spätestens zwei Stunden bereinigt. Die hochgeladene Videodatei wird nur temporär während der Verarbeitung gespeichert und nach dem Uploadversuch entfernt. Die Video-ID wird angezeigt, aber von dieser Verification-App nicht dauerhaft gespeichert.</p>

<h2>Schutzmaßnahmen</h2>
<p>OAuth-Tokens, Client-Secrets und andere sensible Authentifizierungsdaten werden serverseitig gehalten und gegen unbefugten Zugriff geschützt. Client-Secrets werden dem Cloud-Run-Dienst über Secret-Mounts bereitgestellt. Sie werden nicht an das Frontend ausgeliefert, nicht in diesem Repository gespeichert und nicht in Anwendungslogs geschrieben. Die öffentliche Anwendung verwendet HTTPS, sichere und HTTP-only Sitzungs-Cookies, kurzlebige OAuth-Zustände, CSRF-Schutz und restriktive Browser-Sicherheitsheader.</p>

<h2>Zugriff widerrufen</h2>
<p>Du kannst den Zugriff jederzeit in den <a href="https://myaccount.google.com/connections" rel="noopener noreferrer">Verbindungen deines Google-Kontos</a> widerrufen. Nach dem Widerruf kann UNAUFHALTSAM keine weiteren Uploads mit dieser Autorisierung ausführen.</p>

<p><a href="{{ url_for('index') }}">Zurück zur YouTube-Upload-Funktion</a></p>
</main></body></html>"""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Pflichtvariable fehlt: {name}")
    return value


def _max_upload_mb() -> int:
    try:
        value = int(os.environ.get("VERIFICATION_MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB)))
    except ValueError as exc:
        raise RuntimeError("VERIFICATION_MAX_UPLOAD_MB muss eine ganze Zahl sein") from exc
    if not 1 <= value <= MAX_CONFIGURED_UPLOAD_MB:
        raise RuntimeError(f"VERIFICATION_MAX_UPLOAD_MB muss zwischen 1 und {MAX_CONFIGURED_UPLOAD_MB} liegen")
    return value


def _validate_web_client(path: Path, redirect_uri: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Google-Web-OAuth-Client fehlt oder ist ungültig") from exc
    web = payload.get("web")
    required = {"client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris"}
    if not isinstance(web, dict) or not required.issubset(web):
        raise RuntimeError("YOUTUBE_WEB_CLIENT_SECRET_FILE muss einen OAuth-Client vom Typ Webanwendung enthalten")
    if redirect_uri not in web["redirect_uris"]:
        raise RuntimeError("Der konfigurierte OAuth-Callback fehlt im Google-Web-OAuth-Client")


def _oauth_settings(testing: bool) -> tuple[Path, str]:
    secret_file = Path(_required_env("YOUTUBE_WEB_CLIENT_SECRET_FILE")).resolve()
    redirect_uri = _required_env("YOUTUBE_OAUTH_REDIRECT_URI")
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("YOUTUBE_OAUTH_REDIRECT_URI muss eine absolute HTTPS-URL sein")
    if not testing and redirect_uri != PUBLIC_REDIRECT_URI:
        raise RuntimeError(f"YOUTUBE_OAUTH_REDIRECT_URI muss {PUBLIC_REDIRECT_URI} entsprechen")
    _validate_web_client(secret_file, redirect_uri)
    return secret_file, redirect_uri


def _db_path() -> Path:
    return Path(os.environ.get("VERIFICATION_DB", "verification-data/sessions.sqlite3")).resolve()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS oauth_sessions (session_id TEXT PRIMARY KEY, credentials_json TEXT, created_at TEXT NOT NULL)")
    db.execute("DELETE FROM oauth_sessions WHERE created_at < ?", ((datetime.now(UTC) - SESSION_TTL).isoformat(),))
    db.commit()
    return db


def _store_credentials(session_id: str, credentials: Credentials) -> None:
    with closing(_connect()) as db:
        db.execute(
            "INSERT INTO oauth_sessions VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET credentials_json=excluded.credentials_json,created_at=excluded.created_at",
            (session_id, credentials.to_json(), datetime.now(UTC).isoformat()),
        )
        db.commit()


def _load_credentials(session_id: str) -> Credentials | None:
    with closing(_connect()) as db:
        row = db.execute("SELECT credentials_json,created_at FROM oauth_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not row or datetime.fromisoformat(row[1]) < datetime.now(UTC) - SESSION_TTL:
        return None
    return Credentials.from_authorized_user_info(json.loads(row[0]), SCOPES)


def _delete_credentials(session_id: str) -> None:
    if not session_id:
        return
    with closing(_connect()) as db:
        db.execute("DELETE FROM oauth_sessions WHERE session_id=?", (session_id,))
        db.commit()


def _is_mp4(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 12:
        return False
    with path.open("rb") as handle:
        header = handle.read(12)
    return header[4:8] == b"ftyp"


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.secret_key = _required_env("VERIFICATION_SESSION_SECRET")
    if not testing and len(app.secret_key) < 32:
        raise RuntimeError("VERIFICATION_SESSION_SECRET muss mindestens 32 Zeichen lang sein")
    max_upload_mb = _max_upload_mb()
    app.config.update(
        MAX_CONTENT_LENGTH=max_upload_mb * 1024 * 1024,
        TESTING=testing,
        SESSION_COOKIE_NAME="unaufhaltsam_verification",
        SESSION_COOKIE_SECURE=not testing,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=SESSION_TTL,
    )

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if not testing:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/")
    def index():
        return render_template_string(PAGE, step="start")

    @app.get("/privacy")
    def privacy():
        return render_template_string(PRIVACY_PAGE)

    @app.get("/oauth/start")
    def oauth_start():
        secret_file, redirect_uri = _oauth_settings(testing)
        flow = Flow.from_client_secrets_file(secret_file, scopes=SCOPES, redirect_uri=redirect_uri)
        authorization_url, state = flow.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="false"
        )
        code_verifier = flow.code_verifier
        if not code_verifier:
            raise RuntimeError("OAuth PKCE code verifier wurde nicht erzeugt")
        session.clear()
        session.permanent = True
        session["oauth_state"] = state
        session["oauth_state_created_at"] = datetime.now(UTC).isoformat()
        session["oauth_code_verifier"] = code_verifier
        session["session_id"] = secrets.token_urlsafe(32)
        return redirect(authorization_url)

    @app.get("/oauth/callback")
    def oauth_callback():
        state = session.get("oauth_state")
        created_at = session.get("oauth_state_created_at")
        code_verifier = session.get("oauth_code_verifier")
        if not state or not created_at or not code_verifier or request.args.get("state") != state:
            abort(400, "OAuth state ist ungültig")
        try:
            if datetime.fromisoformat(created_at) < datetime.now(UTC) - STATE_TTL:
                abort(400, "OAuth state ist abgelaufen")
        except ValueError:
            abort(400, "OAuth state ist ungültig")
        if request.args.get("error"):
            abort(400, "Google-Autorisierung wurde nicht abgeschlossen")
        secret_file, redirect_uri = _oauth_settings(testing)
        flow = Flow.from_client_secrets_file(
            secret_file,
            scopes=SCOPES,
            state=state,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        flow.fetch_token(authorization_response=request.url)
        granted_scopes = set(flow.credentials.scopes or SCOPES)
        if granted_scopes != {YOUTUBE_UPLOAD_SCOPE}:
            abort(400, "Unerwartete OAuth-Scopes")
        _store_credentials(session["session_id"], flow.credentials)
        session.pop("oauth_state", None)
        session.pop("oauth_state_created_at", None)
        session.pop("oauth_code_verifier", None)
        session["csrf"] = secrets.token_urlsafe(32)
        return render_template_string(PAGE, step="upload", csrf=session["csrf"], max_upload_mb=max_upload_mb)

    @app.post("/upload")
    def upload():
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "missing")):
            abort(400, "CSRF-Prüfung fehlgeschlagen")
        session_id = session.get("session_id", "")
        credentials = _load_credentials(session_id)
        if not credentials:
            return redirect(url_for("oauth_start"))
        uploaded = request.files.get("video")
        filename = secure_filename(uploaded.filename if uploaded else "")
        if not uploaded or not filename.lower().endswith(".mp4"):
            abort(400, "Nur MP4-Dateien sind erlaubt")
        if uploaded.mimetype not in {"video/mp4", "application/mp4", "application/octet-stream"}:
            abort(400, "Ungültiger MP4-Dateityp")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title or len(title) > 100 or len(description) > 5000:
            abort(400, "Titel oder Beschreibung ist ungültig")
        try:
            with tempfile.TemporaryDirectory(prefix="unaufhaltsam-upload-") as directory:
                video_path = Path(directory) / "upload.mp4"
                uploaded.save(video_path)
                if not _is_mp4(video_path):
                    abort(400, "Datei ist kein gültiger MP4-Container")
                metadata = {
                    "title": title,
                    "description": description,
                    "tags": ["UNAUFHALTSAM", "Gameplay"],
                    "video_path": str(video_path),
                    "privacy_status": "private",
                }
                response = upload_video_with_credentials(metadata, credentials)
            video_id = response.get("id")
            if not video_id:
                raise RuntimeError("YouTube hat keine Video-ID geliefert")
            return render_template_string(PAGE, step="done", video_id=video_id)
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.error("Nutzerinitiierter YouTube-Upload fehlgeschlagen (%s)", type(exc).__name__)
            return render_template_string(PAGE, step="error"), 502
        finally:
            _delete_credentials(session_id)
            session.clear()

    @app.errorhandler(413)
    def upload_too_large(_error):
        return f"Upload überschreitet das Limit von {max_upload_mb} MB", 413

    @app.get("/health")
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "scope": YOUTUBE_UPLOAD_SCOPE}

    return app


def main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
