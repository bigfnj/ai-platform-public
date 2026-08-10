@echo off
rem Launch the edu-suite dashboard and open it in the browser.
cd /d "%~dp0"

rem Where finished work is stored (change if you like).
if "%EDU_LIBRARY_DIR%"=="" set "EDU_LIBRARY_DIR=D:\edu-suite-library"

where uv >nul 2>nul
if not %errorlevel%==0 (
  echo Could not find 'uv' on PATH. Install uv, or ask your setup helper.
  pause
  exit /b 1
)

echo Starting the dashboard... (leave this window open; close it to stop)
start "edu-suite dashboard" cmd /c "uv run python serve.py"
timeout /t 8 /nobreak >nul
start "" "http://127.0.0.1:8800/"
