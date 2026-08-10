# Build BrokerTray.exe with the built-in .NET Framework compiler (no SDK needed).
# The tray icon is currently drawn in code (a flat colored status dot), so no image
# resources are embedded. icons\*.png + ollama-icon.svg are kept for a future real
# icon (re-add the /resource: args + LoadIcons/IconFromRes to use them).
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "csc not found at $csc (.NET Framework 4.x required)" }
$out = Join-Path $PSScriptRoot 'BrokerTray.exe'
$src = Join-Path $PSScriptRoot 'BrokerTray.cs'
& $csc /nologo /target:winexe /out:$out `
    /reference:System.Windows.Forms.dll `
    /reference:System.Drawing.dll `
    /reference:System.Web.Extensions.dll `
    $src
if ($LASTEXITCODE -eq 0) { Write-Host "built: $out" -ForegroundColor Green }
else { throw "csc failed ($LASTEXITCODE)" }