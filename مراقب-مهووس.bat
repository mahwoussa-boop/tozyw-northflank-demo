@echo off
chcp 65001 >nul
REM ============================================================
REM  زر المالك: تثبيت «مراقب مهووس» — يبقي الخادم حيّاً بلا توقّف
REM  يسجّل مهمة مجدولة تفحص نبض الخادم كل دقيقتين، وتعيد تشغيله
REM  تلقائياً إن توقّف أو تجمّد. شغّله مرّة واحدة فقط على جهاز الخادم.
REM  للإلغاء لاحقاً:  schtasks /Delete /TN "MahwousWatchdog" /F
REM ============================================================

REM ---- رفع الصلاحيات (لازم لإنشاء مهمة مجدولة) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo   يطلب صلاحيات المسؤول... اضغط "نعم" في نافذة UAC.
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

set "PS=%~dp0scripts\health_watchdog.ps1"

echo.
echo تثبيت مراقب الصحّة (يفحص كل دقيقتين)...
schtasks /Create /TN "MahwousWatchdog" ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%PS%\"" ^
  /SC MINUTE /MO 2 /RL HIGHEST /F

if %errorlevel% equ 0 (
    echo.
    echo   تم — المراقب يعمل الآن. الخادم سيُعاد تشغيله تلقائياً إن توقّف.
) else (
    echo.
    echo   فشل التثبيت — تأكّد أنك شغّلت الملف كمسؤول.
)
echo.
pause
