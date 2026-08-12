@echo off
REM ==== Mahwous nightly competitor scrape (scheduled before radar rebuild) ====
REM Runs the same ScraperService.scrape_all() the manual "start scrape" button calls.
REM Safe: uses scrape_all's own .scrape.lock, skips quietly if a manual scrape is live.
REM Logs to data\logs\nightly_scrape.log.
chcp 65001 >nul
cd /d "%~dp0"
set "DATA_DIR=%~dp0data"
if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"
".venv\Scripts\python.exe" -X utf8 "scripts\run_nightly_scrape.py" >> "%~dp0data\logs\nightly_scrape.log" 2>&1
