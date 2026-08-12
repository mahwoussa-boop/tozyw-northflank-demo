@echo off
REM ============================================================
REM  Enable auto-start on Windows boot (run once on the PC
REM  that should host the app, e.g. the boss's machine).
REM ============================================================
chcp 65001 >nul
title Mahwous - Enable Auto Start

set "TARGET=%~dp0تشغيل-مهووس.bat"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\Mahwous.lnk'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"

if exist "%STARTUP%\Mahwous.lnk" (
    echo.
    echo   DONE. The app will start automatically when Windows starts.
    echo   To disable: delete this file:
    echo   %STARTUP%\Mahwous.lnk
) else (
    echo   Could not create the startup shortcut.
)
echo.
pause
