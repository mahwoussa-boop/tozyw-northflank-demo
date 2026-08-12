@echo off

REM ================================================================
REM  Auto-Elevation: Request Admin Rights if not already admin
REM ================================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title Mahwous Pricing v2 - LAN Server

set "DATA_DIR=%~dp0data"

echo.
echo ========================================================
echo    Mahwous - Smart Pricing v2  (LAN Server)
echo    Do NOT close this window!
echo ========================================================
echo.

REM ================================================================
REM  Auto-Firewall: Open Port 8502 TCP Inbound
REM ================================================================
echo [1/4] Configuring Windows Firewall (Port 8502)...

netsh advfirewall firewall delete rule name="Mahwous Pricing LAN" >nul 2>&1
netsh advfirewall firewall add rule name="Mahwous Pricing LAN" dir=in action=allow protocol=TCP localport=8502 >nul 2>&1

if %errorlevel% equ 0 (
    echo       OK - Port 8502 is now open.
) else (
    echo       WARNING - Could not configure firewall.
)
echo.

REM ================================================================
REM  Keep Awake: Prevent Sleep Mode while server is running
REM ================================================================
echo [2/4] Activating Keep-Awake mode (no sleep)...

powershell -WindowStyle Hidden -Command ^
  "$job = Start-Job -ScriptBlock { Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class KA { [DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f); }'; while($true) { [KA]::SetThreadExecutionState(0x80000003) | Out-Null; Start-Sleep -Seconds 30 } }; $job.Id" ^
  > "%TEMP%\mahwous_awake_pid.txt" 2>nul

start /b powershell -WindowStyle Hidden -Command ^
  "Add-Type 'using System; using System.Runtime.InteropServices; public class KA { [DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f); }'; while($true) { [KA]::SetThreadExecutionState(0x80000003) | Out-Null; Start-Sleep 30 }"

echo       OK - Sleep mode disabled while server is running.
echo.

REM ================================================================
REM  Detect Local IPv4 Address
REM ================================================================
echo [3/4] Detecting your local IP address...
echo.

set "LOCAL_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do (
        set "LOCAL_IP=%%b"
    )
)

if not defined LOCAL_IP (
    echo       WARNING - Could not detect IP. Using localhost.
    set "LOCAL_IP=127.0.0.1"
)

echo ========================================================
echo.
echo   Send this link to your employee:
echo.
echo        http://%LOCAL_IP%:8502
echo.
echo   Or open locally:
echo        http://localhost:8502
echo.
echo ========================================================
echo.

REM ================================================================
REM  Launch Streamlit
REM ================================================================
echo [4/4] Starting the application...
echo.

python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8502 --server.headless true
if errorlevel 1 (
    echo.
    echo Trying py launcher...
    py -m streamlit run app.py --server.address 0.0.0.0 --server.port 8502 --server.headless true
    if errorlevel 1 (
        echo.
        echo ERROR: Could not start the app.
        echo Make sure Python and Streamlit are installed:
        echo   pip install streamlit
    )
)

REM ================================================================
REM  Cleanup: Restore sleep mode on exit
REM ================================================================
echo.
echo Restoring power settings...
taskkill /f /im powershell.exe /fi "WINDOWTITLE eq mahwous_keep_awake" >nul 2>&1
powershell -Command "Get-Process powershell -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowHandle -eq 0} | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1

echo App stopped. Press any key to close.
pause >nul
