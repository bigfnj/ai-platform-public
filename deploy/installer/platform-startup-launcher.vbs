' Silent launcher for platform-startup.ps1
' WScript.Run window-style 0 = completely hidden (no taskbar button, no console).
' WshShortcut.WindowStyle cannot express "hidden"; this file is the workaround.
Dim sh, script
Set sh = CreateObject("WScript.Shell")
script = Replace(WScript.ScriptFullName, "platform-startup-launcher.vbs", "platform-startup.ps1")
sh.Run "powershell.exe -NonInteractive -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File """ & script & """", 0, False
