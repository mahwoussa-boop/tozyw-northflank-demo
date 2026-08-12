@echo off
REM ==== فتح «مهووس» في كروم كتطبيق مستقل — انقر نقراً مزدوجاً ====
REM لماذا أخفّ من نافذة كروم عادية:
REM   • ملفّ شخصي مخصّص (--user-data-dir): بلا إضافات ولا تبويبات محمّلة.
REM     الإضافات هي السبب الأشيع لثقل المتصفّح، لا الصفحة نفسها.
REM   • وضع التطبيق (--app): بلا شريط عنوان ولا تبويبات — ذاكرة أقل ومظهر برنامج.
REM لا يغيّر أي شيء في التطبيق أو بياناتك؛ مجرد طريقة فتح.
chcp 65001 >nul
set "PROFILE=%LOCALAPPDATA%\MahwousChrome"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME%" (
    echo [!] لم أجد كروم — سيُفتح بالمتصفّح الافتراضي بدلاً منه.
    start "" "http://localhost:8502"
    exit /b 0
)

start "" "%CHROME%" --user-data-dir="%PROFILE%" --app="http://localhost:8502" --window-size=1500,950 --no-first-run --no-default-browser-check
exit /b 0
