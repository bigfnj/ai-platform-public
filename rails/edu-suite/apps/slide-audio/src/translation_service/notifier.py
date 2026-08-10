import subprocess


def notify(title: str, body: str) -> None:
    """
    Send a Windows balloon notification from WSL via PowerShell.
    Fire-and-forget — does not block the pipeline.
    Silently skips if powershell.exe is unavailable (e.g. pure Linux).
    """
    try:
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            "$n.Visible = $true; "
            f"$n.ShowBalloonTip(8000, '{_esc(title)}', '{_esc(body)}', "
            "[System.Windows.Forms.ToolTipIcon]::Info); "
            "Start-Sleep 9; "
            "$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell.exe", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass  # not running under WSL / Windows


def _esc(s: str) -> str:
    """Escape single quotes for PowerShell string interpolation."""
    return s.replace("'", "''")
