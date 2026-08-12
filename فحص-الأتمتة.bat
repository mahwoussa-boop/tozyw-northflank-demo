@echo off
REM ==== فحص صحّة أتمتة «مهووس» — انقر نقراً مزدوجاً ====
REM يتحقّق أن مهام الليلة أنجزت عملها فعلاً (لا مجرد رمز خروج 0).
REM قراءة فقط: لا يكتب في أي قاعدة بيانات ولا يعدّل أي مهمة مجدولة.
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 "scripts\report_automation_health.py"
echo.
pause
