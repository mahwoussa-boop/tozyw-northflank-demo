@echo off
REM ==== Mahwous daily database backup (scheduled ~03:00, before radar rebuild 04:30) ====
REM Consistent snapshot via sqlite backup API, read-only on source. Logs to data\logs\backup_daily.log.
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"
".venv\Scripts\python.exe" -X utf8 "scripts\backup_data.py" daily >> "%~dp0data\logs\backup_daily.log" 2>&1
".venv\Scripts\python.exe" -X utf8 "scripts\rotate_backups.py" >> "%~dp0data\logs\backup_daily.log" 2>&1
