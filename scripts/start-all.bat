@echo off
REM Start the full AllMai stack (each in its own minimized window).
REM Prerequisites: PostgreSQL on :5433 (see README section 1), Redis on :6379.
REM Port guards: never start a second copy of a service that is already up
REM (a duplicate API on :8000 previously shadowed the real one; the worker
REM must always match the API code, so restart both together after a pull).
set ROOT=%~dp0..
call :ensure_port 8000 "allmai-api" "%ROOT%\backend" "%ROOT%\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
call :ensure_worker
call :ensure_port 3001 "allmai-web" "%ROOT%\web" "cmd /c npm run dev -- --port 3001"
echo Done. Check http://127.0.0.1:8000/health
goto :eof

:ensure_port
REM %1=port %2=title %3=workdir %4=command — start only if the port is free.
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', %1); $c.Close(); exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto :port_busy
start "%2" /min /d "%~3" %~4
echo Started %2.
goto :eof

:port_busy
echo Port %1 already in use, skipping start. Kill the stale process first.
goto :eof

:ensure_worker
REM Single worker only: kill a stale one, then start fresh (new code).
taskkill /FI "WINDOWTITLE eq allmai-worker" /F >nul 2>&1
start "allmai-worker" /min /d "%ROOT%\backend" "%ROOT%\.venv\Scripts\celery.exe" -A workers.celery_app.celery worker --loglevel=info --pool=solo
echo Started allmai-worker.
goto :eof
