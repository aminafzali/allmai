@echo off
REM Start the full AllMai stack (each in its own minimized window).
REM Prerequisites: PostgreSQL on :5433 (see README section 1), Redis on :6379.
set ROOT=%~dp0..
start "allmai-api" /min /d "%ROOT%\backend" "%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
start "allmai-worker" /min /d "%ROOT%\backend" "%ROOT%\.venv\Scripts\celery.exe" -A workers.celery_app.celery worker --loglevel=info --pool=solo
start "allmai-web" /min /d "%ROOT%\web" cmd /c "npm run dev -- --port 3001"
echo Started: API :8000, worker, web :3001. Check http://127.0.0.1:8000/health
