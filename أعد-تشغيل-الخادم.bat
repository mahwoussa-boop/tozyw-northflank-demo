@echo off
REM ==== زر المالك: أعد تشغيل الخادم بعد تحديث الكود — انقر نقراً مزدوجاً ====
REM لماذا يلزم: Streamlit يُبقي الوحدات المستوردة (صفحات الواجهة) في ذاكرة
REM العملية كما حُمّلت أول مرّة، و runOnSave غير مضبوط — فأي تحديث للكود لا
REM يظهر لك حتى إعادة تشغيل الخادم. مراقب الصحّة يعيد التشغيل عند توقّف
REM الخادم فقط، لا عند تغيّر الكود.
REM لا يمسّ أي قاعدة بيانات ولا يحذف بيانات؛ يوقف الخادم ويعيده فقط.
chcp 65001 >nul

REM ---- رفع الصلاحيات (مهمة MahwousServer مسجّلة بصلاحيات مرتفعة) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo   يطلب صلاحيات المسؤول... اضغط "نعم" في نافذة UAC.
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title اعادة تشغيل مهووس
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_server.ps1"
echo.
pause
