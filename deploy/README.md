# Cloud-Run-Deployment auf `upload.eddiesworld.de`

Die Verification-App läuft als eigener Cloud-Run-Service auf der Subdomain
`upload.eddiesworld.de`. Die Apex-Domain `eddiesworld.de`, ihre Netcup-
Nameserver und die vorhandene GitHub-Pages-Konfiguration werden nicht geändert.
Cloudflare, ein Pfadproxy und `BACKEND_ORIGIN` werden nicht verwendet.

## Cloud Run

- Service: `unaufhaltsam-youtube-verification`
- Region: `europe-west1` (unterstützt direktes Cloud-Run-Domain-Mapping)
- öffentlich erreichbar: ja
- Port: `8080`
- CPU/Memory: `1 CPU`, `512 MiB`
- Minimum instances: `0`
- Maximum instances: `1`
- Concurrency: `10`
- Request timeout: `900` Sekunden
- Custom Domain: `upload.eddiesworld.de`

Die beiden Geheimnisse gehören in Google Secret Manager:

- `unaufhaltsam-verification-session-secret`: zufälliger Wert mit mindestens 32 Zeichen
- `unaufhaltsam-google-oauth-web-client`: vollständige JSON-Datei des Google-OAuth-Clients vom Typ Webanwendung

Die OAuth-Client-Datei wird nach `/secrets/google-oauth-web-client.json`
gemountet; `VERIFICATION_SESSION_SECRET` wird direkt aus dem Session-Secret
bezogen. Alle übrigen Variablen stehen in `.env.example`.

Das App-Limit ist bewusst auf 25 MB voreingestellt, damit der Upload unter der
32-MiB-Grenze für HTTP/1-Anfragen an Cloud Run bleibt.

## Custom Domain und Netcup-DNS

Nach dem Service-Deployment wird das Mapping angelegt:

```text
gcloud domains verify eddiesworld.de
gcloud beta run domain-mappings create --service unaufhaltsam-youtube-verification --domain upload.eddiesworld.de --region europe-west1
gcloud beta run domain-mappings describe --domain upload.eddiesworld.de --region europe-west1
```

Der letzte Befehl liefert `resourceRecords`. Bei Netcup wird ausschließlich
jeder dort ausgegebene Record gesetzt. Für eine Subdomain liefert Cloud Run
normalerweise genau diesen zusätzlichen Eintrag:

- Host/Name: `upload`
- Typ: `CNAME`
- Ziel/Wert: der von Cloud Run unter `resourceRecords.rrdata` ausgegebene Wert
  (typischerweise `ghs.googlehosted.com.`)
- TTL: `3600` oder Netcup-Standard

Die von Cloud Run tatsächlich ausgegebenen Werte sind verbindlich. Es werden
keine Apex-A/AAAA-Records und keine Nameserver geändert. Das verwaltete
TLS-Zertifikat wird nach wirksamem DNS-Eintrag automatisch ausgestellt.

## Google-Web-OAuth-Client

- Authorized JavaScript origin: `https://upload.eddiesworld.de`
- Authorized redirect URI: `https://upload.eddiesworld.de/oauth/callback`
- Scope: ausschließlich `https://www.googleapis.com/auth/youtube.upload`

