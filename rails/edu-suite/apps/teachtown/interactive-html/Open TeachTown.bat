@echo off
cd /d "%~dp0"

rem Find node: prefer PATH, fall back to the standard Windows install location.
where node >nul 2>nul
if %errorlevel%==0 (
  set "NODE_EXE=node"
) else if exist "%ProgramFiles%\nodejs\node.exe" (
  set "NODE_EXE=%ProgramFiles%\nodejs\node.exe"
) else (
  echo Node.js was not found. Install it from https://nodejs.org and try again.
  pause
  exit /b 1
)

start "TeachTown Server" /min "%NODE_EXE%" serve.js
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/"
