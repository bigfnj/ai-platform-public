<#
  Seamless Ollama updater for BrokerTray (Mode A: install on next restart).

  Ollama here runs as an NSSM Windows service and its installer is Inno Setup, so a normal
  manual update means: download a 1.5 GB MSI, let the installer nag about closing ollama /
  explorer, install, and hope the service restarts. This automates it with zero friction.

    -Mode Stage   (elevated, launched by the tray): download OllamaSetup.exe for -Tag, copy
                  this script next to it under %ProgramData%\BrokerTray\ollama-update, and
                  register a one-shot SYSTEM "at startup" scheduled task to apply it next boot.
    -Mode Apply   (run by that task, as SYSTEM at startup — Ollama isn't busy yet): stop the
                  ollama service, silent-install into the service's existing install dir,
                  restart the service, then self-clean (remove the task + staged installer).

  Inno Setup silent switches: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS
  /DIR=<installdir> (force the location so a SYSTEM-run install updates the user's Ollama)
  /LOG=<file> (diagnostics).
#>
[CmdletBinding()]
param(
  [ValidateSet('Stage', 'Apply')][string]$Mode = 'Stage',
  [string]$Tag = '',
  [string]$Service = 'ollama',
  [string]$InstallDir = ''
)
$ErrorActionPreference = 'Stop'
$TaskName  = 'BrokerTray-OllamaUpdate'
$Work      = Join-Path $env:ProgramData 'BrokerTray\ollama-update'
$Installer = Join-Path $Work 'OllamaSetup.exe'
$SelfCopy  = Join-Path $Work 'update-ollama.ps1'
$Marker    = Join-Path $Work 'STAGED'
$Log       = Join-Path $Work 'update.log'

function Log($m) {
  $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  try { $line | Out-File -FilePath $Log -Append -Encoding utf8 } catch {}
}

function Get-OllamaInstallDir($svc) {
  # NSSM service: the real app path lives under Parameters\Application.
  try {
    $app = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$svc\Parameters" -Name Application -ErrorAction Stop).Application
    if ($app) { return (Split-Path -Parent $app) }
  } catch {}
  $c = Get-Command ollama -ErrorAction SilentlyContinue
  if ($c) { return (Split-Path -Parent $c.Source) }
  return (Join-Path $env:LOCALAPPDATA 'Programs\Ollama')
}

if ($Mode -eq 'Stage') {
  New-Item -ItemType Directory -Force -Path $Work | Out-Null
  Log "=== STAGE $Tag (service=$Service) ==="
  if (-not $InstallDir) { $InstallDir = Get-OllamaInstallDir $Service }
  Log "install dir: $InstallDir"

  $url = "https://github.com/ollama/ollama/releases/download/$Tag/OllamaSetup.exe"
  Log "downloading $url"
  $wc = New-Object System.Net.WebClient      # faster than IWR for a big file
  $wc.DownloadFile($url, $Installer)
  Log ('downloaded {0:N0} bytes' -f (Get-Item $Installer).Length)

  Copy-Item -LiteralPath $PSCommandPath -Destination $SelfCopy -Force  # survive repo moves

  $applyArg = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode Apply -Service {1} -InstallDir "{2}"' -f $SelfCopy, $Service, $InstallDir
  $action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $applyArg
  $trigger   = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest  # LocalSystem
  $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                         -Principal $principal -Settings $settings -Force | Out-Null
  Set-Content -LiteralPath $Marker -Value $Tag -Encoding utf8
  Log "scheduled task '$TaskName' registered (at startup, SYSTEM). Installs on next restart."
}
elseif ($Mode -eq 'Apply') {
  Log "=== APPLY (service=$Service, dir=$InstallDir) ==="
  try {
    if (-not (Test-Path $Installer)) { Log 'installer missing; abort'; return }
    Log "stopping $Service"
    Stop-Service -Name $Service -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 20 -and (Get-Service $Service -ErrorAction SilentlyContinue).Status -ne 'Stopped'; $i++) { Start-Sleep 1 }
    # belt-and-suspenders: kill any lingering ollama processes so no files are locked
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like 'ollama*' } | Stop-Process -Force -ErrorAction SilentlyContinue

    $args = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', "/LOG=$Log.inno.log")
    if ($InstallDir) { $args += "/DIR=$InstallDir" }
    Log ('running installer: ' + ($args -join ' '))
    $p = Start-Process -FilePath $Installer -ArgumentList $args -Wait -PassThru
    Log "installer exit code $($p.ExitCode)"

    # The Ollama installer auto-launches its own 'ollama app' (which grabs :11434) and re-adds a
    # login autostart; kill both so OUR NSSM service is the sole host and can bind the port.
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like 'ollama*' } | Stop-Process -Force -ErrorAction SilentlyContinue
    foreach ($hive in 'HKLM:\SOFTWARE', 'HKCU:\SOFTWARE') {
      try { Remove-ItemProperty "$hive\Microsoft\Windows\CurrentVersion\Run" -Name 'Ollama' -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep 2

    Start-Service -Name $Service -ErrorAction SilentlyContinue
    Log "started $Service"
  } catch {
    Log "ERROR: $($_.Exception.Message)"
    try { Start-Service -Name $Service -ErrorAction SilentlyContinue } catch {}
  } finally {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Marker -Force -ErrorAction SilentlyContinue
    Log 'cleanup done'
  }
}
