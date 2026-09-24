<#
.SYNOPSIS
  Start the Course Builder rail backend natively (no Docker required).

.DESCRIPTION
  Runs uvicorn directly on the Windows host so the backend can read arbitrary
  local directories without volume-mount configuration. The frontend still
  builds with Vite and is served by the gateway (same as all other rails).

.EXAMPLE
  # Minimal — broker on default port, no blueprint CSV:
  .\start-native.ps1

  # Full — point at your blueprint CSV and an alternate index path:
  .\start-native.ps1 `
    -BlueprintCsv "C:\Users\you\OneDrive - Accenture\Projects\ServiceNOW\ai-notes\research\blueprint-domains.csv" `
    -IndexPath    "C:\Users\you\.course-builder-index.duckdb"
#>

param(
  [string]$BrokerUrl    = 'http://127.0.0.1:11500',
  [string]$BlueprintCsv = '',
  [string]$IndexPath    = (Join-Path $HOME '.course-builder-index.duckdb'),
  [string]$EmbedRole    = '@embed',
  [string]$CondenseRole = '@openmaic',
  [int]   $Port         = 8901
)

$backendDir = Join-Path $PSScriptRoot '..\backend'
Push-Location $backendDir

# Install if needed (idempotent; pip skips up-to-date packages).
pip install -q -e .

$env:CB_BROKER_URL    = $BrokerUrl
$env:CB_BLUEPRINT_CSV = $BlueprintCsv
$env:CB_INDEX_PATH    = $IndexPath
$env:CB_EMBED_ROLE    = $EmbedRole
$env:CB_CONDENSE_ROLE = $CondenseRole
$env:PLATFORM_STANDALONE = '1'   # skip gateway identity gate in dev

Write-Host "Course Builder backend -> http://127.0.0.1:$Port"
Write-Host "  Blueprint CSV : $($BlueprintCsv -or '(not set)')"
Write-Host "  Index path    : $IndexPath"
Write-Host "  Embed role    : $EmbedRole"
Write-Host "  Condense role : $CondenseRole"
Write-Host ""
Write-Host "Frontend dev server (after npm install + npm run dev in frontend/):"
Write-Host "  http://127.0.0.1:5351"
Write-Host ""

uvicorn course_builder_app.api.app:app --host 127.0.0.1 --port $Port --reload

Pop-Location
