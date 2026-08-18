param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Image,
    [string]$Region = "europe-west1"
)

$ErrorActionPreference = "Stop"
$service = "unaufhaltsam-youtube-verification"
$domain = "upload.eddiesworld.de"

gcloud config set project $ProjectId
gcloud run deploy $service `
    --image $Image `
    --region $Region `
    --platform managed `
    --allow-unauthenticated `
    --port 8080 `
    --memory 512Mi `
    --cpu 1 `
    --min-instances 0 `
    --max-instances 1 `
    --concurrency 10 `
    --timeout 900 `
    --set-env-vars "PUBLISH_MODE=dry_run,YOUTUBE_WEB_CLIENT_SECRET_FILE=/secrets/google-oauth-web-client.json,YOUTUBE_OAUTH_REDIRECT_URI=https://upload.eddiesworld.de/oauth/callback,VERIFICATION_DB=/tmp/unaufhaltsam-verification.sqlite3,VERIFICATION_MAX_UPLOAD_MB=25" `
    --set-secrets "VERIFICATION_SESSION_SECRET=unaufhaltsam-verification-session-secret:latest,/secrets/google-oauth-web-client.json=unaufhaltsam-google-oauth-web-client:latest"

gcloud beta run domain-mappings create --service $service --domain $domain --region $Region
gcloud beta run domain-mappings describe --domain $domain --region $Region
