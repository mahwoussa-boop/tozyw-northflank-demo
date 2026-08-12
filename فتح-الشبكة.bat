@echo off
chcp 65001 >nul
REM ============================================================
REM  زر المالك: فتح منفذ الشبكة (8502) لمرة واحدة
REM  يرفع صلاحياته تلقائياً (UAC) ثم يُنشئ قاعدة جدار ناري دائمة
REM  تسمح للأجهزة الأخرى على نفس الشبكة بفتح مهووس.
REM  شغّله مرة واحدة فقط على جهاز الخادم.
REM ============================================================

REM ---- رفع الصلاحيات (لازم لتعديل الجدار الناري) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo   يطلب صلاحيات المسؤول... اضغط "نعم" في نافذة UAC.
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo.
echo فتح منفذ الشبكة 8502 لكل الأجهزة على نفس الشبكة...
powershell -NoProfile -ExecutionPolicy Bypass -Command "if(-not(Get-NetFirewallRule -DisplayName 'Mahwous Pricing LAN' -ErrorAction SilentlyContinue)){New-NetFirewallRule -DisplayName 'Mahwous Pricing LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8502 -Profile Any | Out-Null}; if(Get-NetFirewallRule -DisplayName 'Mahwous Pricing LAN' -ErrorAction SilentlyContinue){Write-Host '  تم فتح المنفذ بنجاح.'}else{Write-Host '  فشل — جرّب مجدداً.'}"

echo.
echo عنوان الوصول من الأجهزة الأخرى (نفس الشبكة):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do echo    http://%%b:8502
)
echo.
echo (اضغط أي زر للإغلاق)
pause >nul
