@echo off
rem Starts the health record app: builds the screens, then runs the server at http://127.0.0.1:8000
rem Double-click to start. Close this window (or press Ctrl+C) to stop.
title Health record app
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo The backend is not set up yet. See the README: create backend\.venv first.
  pause
  exit /b 1
)
if not exist "backend\.env" (
  echo backend\.env is missing. Copy backend\.env.example to backend\.env and add the keys.
  pause
  exit /b 1
)

echo.
echo [1/4] Installing backend packages (quick when nothing changed)...
cd backend
.venv\Scripts\python -m pip install --quiet --disable-pip-version-check -e ".[dev]"
if errorlevel 1 goto failed
cd ..

echo.
echo [2/4] Installing frontend packages (quick when nothing changed)...
cd frontend
call npm.cmd install --no-audit --no-fund --loglevel=error
if errorlevel 1 goto failed

echo.
echo [3/4] Building the screens...
call npm.cmd run build
if errorlevel 1 goto failed
cd ..

echo.
echo [4/4] Starting the server at http://127.0.0.1:8000
echo       The browser opens in a few seconds. To share on Tailscale, run share-tailscale.bat too.
echo.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"
cd backend
.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload
pause
exit /b 0

:failed
echo.
echo Something went wrong above. Read the error, then try again.
pause
exit /b 1
