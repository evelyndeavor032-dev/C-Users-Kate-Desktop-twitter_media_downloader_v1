@echo off
setlocal
cd /d "%~dp0"
title Twitter Media Downloader v3.0.0
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
echo ==========================================
echo   Twitter Media Downloader v3.0.0
echo ==========================================
%PY% -c "import sys; print('Python', sys.version)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  pause
  exit /b 1
)
echo Installing/updating required packages...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed. Check the messages above.
  pause
  exit /b 1
)
echo Installing Playwright Chromium runtime...
%PY% -m playwright install chromium
if errorlevel 1 (
  echo Playwright browser setup failed. The GUI may still open, but browser scanning will not work.
  pause
)
echo Starting application...
%PY% app.py
if errorlevel 1 (
  echo Application exited with an error.
  pause
)
endlocal
