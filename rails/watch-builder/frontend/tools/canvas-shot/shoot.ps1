<#
.SYNOPSIS
  Screenshot the editor's canvas for every preset, so it can be compared with the build.

.DESCRIPTION
  The canvas is the one part of this rail that no automated check reaches. Everything else
  goes through the Pebble SDK, which never executes `Canvas.tsx`. Two canvas defects shipped
  past a full suite, conformance, smoke and eight verified emulator renders, and both were
  found by eye. This is how you look.

  Writes two PNGs: one with nothing selected, and one with element 0 selected, because the
  z-order defect only appeared while something was selected.

.PARAMETER Refresh
  Re-capture fixtures.json from the running rail before building. Needs the
  platform-watch-builder container up. Without this it uses whatever was last captured, so a
  shot can be taken with no stack running.

.EXAMPLE
  .\shoot.ps1
  .\shoot.ps1 -Refresh -Zoom 3
#>
[CmdletBinding()]
param(
  [switch]$Refresh,
  [int]$Port = 8791,
  [int]$Zoom = 2,
  [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
# Resolved here, not as the param() default: Windows PowerShell 5.1 started with -File leaves
# $PSScriptRoot empty while an advanced script's defaults are evaluated, so the shots went to
# \shots at the root of the current drive.
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot 'shots' }
# canvas-shot -> tools -> frontend -> watch-builder -> rails -> repo root. Counted rather
# than guessed: the first version stopped at `rails` and Start-Process failed on a path that
# looked plausible.
$frontend = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$repo = Split-Path (Split-Path (Split-Path $frontend -Parent) -Parent) -Parent
$venvPy = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { throw "no repo venv at $venvPy (derived repo root: $repo)" }

if ($Refresh) {
  Write-Host 'capturing fixtures from the running rail...'
  # Through the gateway container, because the rail's port is not published to the host.
  $script = @'
import json,urllib.request
def req(u, body=None):
    r=urllib.request.Request(u, headers={"X-Platform-User":"shot","Content-Type":"application/json"},
                             data=json.dumps(body).encode() if body else None)
    return json.load(urllib.request.urlopen(r, timeout=30))
out={}
for p in req("http://watch-builder:8920/api/presets")["presets"]:
    rep=req("http://watch-builder:8920/api/validate", p["design"])
    plat=p["design"]["platforms"][0]
    out[p["id"]]={"design":p["design"],"boxes":rep["boxes"][plat],
                  "screen":rep["screens"][plat],"platform":plat}
print(json.dumps(out, indent=1))
'@
  $script | docker exec -i platform-gateway-1 python - |
    Set-Content (Join-Path $PSScriptRoot 'fixtures.json') -Encoding utf8
}

Write-Host 'building the harness...'
Push-Location $frontend
try {
  & npx vite build --config tools/canvas-shot/vite.config.ts | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "vite build failed ($LASTEXITCODE)" }
} finally { Pop-Location }

$dist = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Force $OutDir | Out-Null

# Served over HTTP on purpose. See the note in vite.config.ts: a module script from file://
# is CORS-blocked and renders a blank page with no error on screen.
$server = Start-Process $venvPy -ArgumentList @('-m','http.server',"$Port",'--bind','127.0.0.1') `
  -WorkingDirectory $dist -PassThru -WindowStyle Hidden
try {
  Start-Sleep -Milliseconds 1200
  $chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
  if (-not (Test-Path $chrome)) { throw "no Chrome at $chrome" }
  $profileDir = Join-Path $env:TEMP 'wb-canvas-shot-profile'

  foreach ($mode in @('none','0')) {
    $png = Join-Path $OutDir "canvas-sel-$mode.png"
    $url = "http://127.0.0.1:$Port/index.html?sel=$mode&zoom=$Zoom"
    & $chrome --headless=new --disable-gpu --hide-scrollbars `
      --window-size=2000,1500 --virtual-time-budget=5000 `
      --user-data-dir="$profileDir" --screenshot="$png" $url 2>&1 | Out-Null
    if (Test-Path $png) {
      Write-Host "  sel=$mode -> $png"
    } else {
      throw "chrome produced no screenshot for sel=$mode"
    }
  }
} finally {
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}

Write-Host ''
Write-Host "canvas shots in $OutDir"
Write-Host 'Compare against the emulator renders. Measured after the fidelity pass: 3.2% to'
Write-Host '5.6% of pixels differ, and that band is the floor, not a defect. What makes it up:'
Write-Host '  type     the canvas has no Pebble fonts, so glyph shapes differ in a text box'
Write-Host '  edges    a CSS border-radius circle and a rasterised fill_circle differ by ~1px'
Write-Host '  seconds  the two renders are taken moments apart, so the hands can differ'
Write-Host 'Colour is NOT in that list any more: the canvas draws on-watch colours by default,'
Write-Host 'from the server. Add ?specified=1 to see the raw design values instead.'
Write-Host 'Anything structural, meaning a large contiguous region, is a bug in one of the two.'
