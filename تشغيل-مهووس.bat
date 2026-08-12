@echo off
REM ============================================================
REM  Mahwous Smart Pricing - One-Click Launcher (self-install)
REM  Double-click this file. First run installs everything.
REM ============================================================

REM ---- self-elevate (for firewall + keep-awake) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

REM ---- علامة noweb: لا تفتح صفحة متصفّح. تمرّرها مهمة MahwousServer، لأن
REM      المراقب يعيد التشغيل عند كل فشل نبض فكانت كل مرّة تفتح صفحة جديدة
REM      في وجه المالك (ستّ صفحات في ساعة، 2026-07-25). النقر اليدوي يبقى
REM      كما هو: بلا العلامة ⇒ تُفتح الصفحة. ----
set "NOWEB="
if /i "%~1"=="noweb" set "NOWEB=1"
if /i "%~2"=="noweb" set "NOWEB=1"

REM ---- relaunch minimized (keeps it out of the way; still in the taskbar
REM      if you need to check on it). "min" marker avoids relaunch looping. ----
if not "%~1"=="min" (
    if defined NOWEB (
        start "" /min "%~f0" min noweb
    ) else (
        start "" /min "%~f0" min
    )
    exit /b
)

cd /d "%~dp0"
chcp 65001 >nul
title Mahwous Smart Pricing - Server (do NOT close)

echo.
echo ===============================================
echo    Mahwous Smart Pricing
echo ===============================================
echo.

REM ---- [1/5] find Python ----
echo [1/5] Checking Python...
set "PY="
where py  >nul 2>&1 && set "PY=py"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )

if not defined PY (
    echo      Python not found. Trying winget...
    where winget >nul 2>&1 && (
        winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
        where py >nul 2>&1 && set "PY=py"
        if not defined PY ( where python >nul 2>&1 && set "PY=python" )
    )
)
if not defined PY (
    echo.
    echo   Python is required. Opening the download page...
    echo   IMPORTANT: tick "Add Python to PATH" during install.
    start https://www.python.org/downloads/
    echo   After installing Python, run this file again.
    pause
    exit /b
)
echo      Python OK.

REM ---- [2/5] create/validate virtual env + install libraries ----
REM Validate the venv actually works on THIS machine (a .venv copied from
REM another PC has absolute paths baked in and will fail -> rebuild it).
set "VENV_OK="
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import streamlit" >nul 2>&1 && set "VENV_OK=1"
)
if defined VENV_OK (
    echo [2/5] Libraries already installed and valid. Skipping.
) else (
    echo.
    echo [2/5] Setting up libraries ^(first run or copied from another PC^)...
    if exist ".venv" rmdir /s /q ".venv"
    %PY% -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   ERROR while installing libraries. Check your internet connection
        echo   and run this file again.
        pause
        exit /b
    )
)

REM ---- [3/5] open firewall port 8502 (idempotent + reliable) ----
echo [3/5] Opening firewall ^(port 8502^)...
REM PowerShell New-NetFirewallRule اوثق من netsh؛ ينشئ القاعدة مرة واحدة لكل الشبكات.
powershell -NoProfile -ExecutionPolicy Bypass -Command "if(-not(Get-NetFirewallRule -DisplayName 'Mahwous Pricing LAN' -ErrorAction SilentlyContinue)){New-NetFirewallRule -DisplayName 'Mahwous Pricing LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8502 -Profile Any | Out-Null}" >nul 2>&1
REM احتياط: netsh (يعمل إن غابت وحدة NetSecurity)
netsh advfirewall firewall show rule name="Mahwous Pricing LAN" >nul 2>&1 || netsh advfirewall firewall add rule name="Mahwous Pricing LAN" dir=in action=allow protocol=TCP localport=8502 >nul 2>&1

REM ---- [4/5] keep the PC awake while server runs ----
echo [4/5] Enabling keep-awake...
start /b powershell -WindowStyle Hidden -Command "Add-Type 'using System;using System.Runtime.InteropServices;public class KA{[DllImport(\"kernel32.dll\")]public static extern uint SetThreadExecutionState(uint f);}';while($true){[KA]::SetThreadExecutionState(0x80000003)|Out-Null;Start-Sleep 30}"

REM ---- [5/5] detect IP + launch ----
set "LOCAL_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do set "LOCAL_IP=%%b"
)
if not defined LOCAL_IP set "LOCAL_IP=127.0.0.1"

echo.
echo ====================================================
echo   This PC:        http://localhost:8502
echo   Other devices:  http://%LOCAL_IP%:8502
echo   (same Wi-Fi / network)
echo   Keep this window OPEN while using the app.
echo ====================================================
echo.

set "DATA_DIR=%~dp0data"
if not defined NOWEB start "" http://localhost:8502

REM ---- auto-restart on crash; the stop button (ايقاف-مهووس.bat)
REM      drops data\.stop_requested so the loop exits cleanly ----
if exist "data\.stop_requested" del "data\.stop_requested" >nul 2>&1
:serve_loop
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 0.0.0.0 --server.port 8502 --server.headless true --browser.gatherUsageStats false
if exist "data\.stop_requested" (
    del "data\.stop_requested" >nul 2>&1
    goto server_stopped
)
echo Server stopped unexpectedly - restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto serve_loop
:server_stopped

echo.
echo Server stopped. Press any key to close.
pause >nul
