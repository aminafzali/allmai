@echo off
REM Stop the AllMai stack started by start-all.bat.
taskkill /FI "WINDOWTITLE eq allmai-api" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq allmai-worker" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq allmai-web" /F >nul 2>&1
echo Stopped. (PostgreSQL/Redis services keep running.)
