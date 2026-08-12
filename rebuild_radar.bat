@echo off
REM ==== Mahwous radar-freshness rebuild (scheduled ~04:30, after night scrape) ====
REM Rebuilds the derived opportunity_scores cache from latest competitor data.
REM Atomic + read-only on source. Logs to data\logs\rebuild_radar.log.
chcp 65001 >nul
cd /d "%~dp0"
set "DATA_DIR=%~dp0data"
if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"
".venv\Scripts\python.exe" -X utf8 "scripts\rebuild_radar.py" >> "%~dp0data\logs\rebuild_radar.log" 2>&1
".venv\Scripts\python.exe" -X utf8 "scripts\prune_signal_events.py" >> "%~dp0data\logs\rebuild_radar.log" 2>&1
