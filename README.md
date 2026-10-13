# UNAUFHALTSAM YouTube Autopilot

## Startbefehl

Einmalig lokale Abhängigkeiten einrichten:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
npx playwright install chromium
```

Einen vollständigen echten Bot-Run mit sicherem Publisher-Dry-Run ausführen:

```powershell
$env:PUBLISH_MODE="dry_run"
.\.venv\Scripts\python.exe autopilot.py --once
```

Den begrenzten Tagesworker starten:

```powershell
.\.venv\Scripts\python.exe autopilot.py
```

Der Bot spielt ausschließlich visuell: Screenshots sehen, Bewegungen daraus verfolgen und per Browser-Maus klicken. DOM-Identität, interne Spielzustände und versteckte Daten sind nicht zugelassen. Der Run endet bei sichtbarem Scheitern oder beim Erreichen von THE WALL (30).

Pro Tag wird höchstens ein Short veröffentlicht. Vorrang haben THE WALL bzw. ein neuer Bot-Rekord; ansonsten wird der stärkste echte Tages-Run ausgewählt. Nicht ausgewählte Versuche bleiben für Fail-/Lern-Auswertung erhalten.

## Konfiguration

Alle Werte werden über Umgebungsvariablen gesetzt:

| Variable | Standard | Bedeutung |
|---|---:|---|
| `PUBLISH_MODE` | `dry_run` | `dry_run` oder `public` |
| `MAX_RUNS_PER_DAY` | `20` | Maximale echte Spielversuche pro UTC-Tag |
| `MAX_UPLOADS_PER_DAY` | `1` | Maximale Publisher-Vorgänge pro UTC-Tag |
| `MINIMUM_PUBLISH_SCORE` | `0` | Optionaler Mindestscore für die Tagesauswahl |
| `COOLDOWN_MINUTES` | `30` | Pause zwischen zwei Runs |
| `BROWSER_RETRIES` | `2` | Wiederholungen bei Browser-/Spielfehlern |
| `RUN_TIMEOUT_SECONDS` | `180` | Maximale Dauer eines Runs |
| `BOT_STRATEGY` | `visual_v1` | Nur `visual_v1`; DOM-Identitätsstrategien werden abgewiesen |
| `OUTPUT_DIR` | `output` | Ausgabeordner |
| `AUTOPILOT_DB` | `output/autopilot.sqlite3` | SQLite-Datei |
| `MAX_QUEUE_SIZE` | `50` | Maximale Anzahl unveröffentlichter Queue-Shorts |
| `GAME_URL` | produktive GitHub-Pages-URL | Spiel-URL |

## Wechsel von dry_run zu public

Der OAuth-Desktop-Client liegt unter `secrets/client_secret.json`. Ein Token kann vorab erzeugt werden:

```powershell
.\.venv\Scripts\python.exe -m unaufhaltsam_youtube.auth authorize
```

Nach abgeschlossener Google-Verifizierung und gültigem Token genügt ohne Codeänderung:

```powershell
$env:PUBLISH_MODE="public"
.\.venv\Scripts\python.exe autopilot.py
```

Der verwendete OAuth-Scope ist ausschließlich `https://www.googleapis.com/auth/youtube.upload`.

## Speicherorte

- Rohaufnahmen: `output/recordings/`
- fertige Shorts: `output/videos/`
- Resultate: `output/recordings/*.json`
- Metadaten: `output/metadata/`
- Run-Logs: `output/logs/bot-runs.jsonl`
- Fairness-Beweise: `output/recordings/<attempt>-evidence/events.jsonl` und Screenshot-Frames; SHA-256 für Log und Rohvideo im Run-Ergebnis
- Scheduler-Log: `output/logs/scheduler.log`
- persistente Queue: `output/queue/`
- Datenbank und Systemereignisse: `output/autopilot.sqlite3`

## Fehlerbehebung

- Browser fehlt: `npx playwright install chromium` erneut ausführen.
- Renderfehler: `imageio-ffmpeg` mit `pip install -r requirements.txt` reparieren und freien Speicher prüfen.
- OAuth/API-Fehler: `secrets/client_secret.json`, Token und Google-Verifizierungsstatus prüfen; danach `auth authorize` erneut ausführen.
- Guardrail-Ereignis: Worker nicht neu starten, bevor `system_events` geprüft wurde. Der Runner blockiert Firebase-Schreibrequests.
- AI-vs-THE-WALL V1: Der Bot ermittelt Fokus, Boxpositionen und Tauschbewegungen aus Screenshots und klickt per Browser-Maus. Uneindeutige Wahrnehmung bricht den Versuch ab. DOM-IDs und interne Spiellogik sind gesperrt. Ohne vorhandenes Chromium, Tesseract, Playwright und einen echten aufgezeichneten Lauf ist das Live-Gate **FAIL**; bestandene Unit-Tests ersetzen keinen Run. Alte Queue-Metadaten ohne Eventlog-/Video-Hashes können nicht öffentlich hochgeladen werden.
- Tests: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

## Google-Verifizierungsdemo

Die separate WSGI-App `wsgi:app` stellt den echten Nutzerablauf auf
`https://upload.eddiesworld.de/` bereit. Sie verwendet ausschließlich
`https://www.googleapis.com/auth/youtube.upload` und lädt ausgewählte MP4-Dateien
standardmäßig **privat** auf den Kanal des angemeldeten Nutzers.

Für ein öffentliches Deployment werden folgende Variablen benötigt:

- `VERIFICATION_SESSION_SECRET`
- `YOUTUBE_WEB_CLIENT_SECRET_FILE` (Google OAuth Client vom Typ Webanwendung)
- `YOUTUBE_OAUTH_REDIRECT_URI` (exakt
  `https://upload.eddiesworld.de/oauth/callback`)
- `VERIFICATION_DB` (Pfad zur kurzlebigen serverseitigen OAuth-Session-DB)
- `VERIFICATION_MAX_UPLOAD_MB` (bei Cloud Run: `25`)
- `PORT` (bei Cloud Run: `8080`)

Der Google-Web-OAuth-Client enthält als Authorized JavaScript origin
`https://upload.eddiesworld.de` und als Redirect URI ausschließlich den oben
genannten Callback.

Das Cloud-Run-Deployment samt direktem Subdomain-Mapping ist unter `deploy/`
beschrieben. Es ist kein Pfadproxy und keine Nameserver-Migration erforderlich.

Die Informationsseite verlinkt sichtbar auf `https://upload.eddiesworld.de/`.
Die öffentliche Datenschutzrichtlinie der Verification-App ist unter
`https://upload.eddiesworld.de/privacy` erreichbar und wird auf der Startseite
verlinkt.
Der lokale Autopilot, sein Desktop-OAuth-Client und `PUBLISH_MODE=dry_run` bleiben
davon unabhängig.
