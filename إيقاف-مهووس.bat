@echo off
chcp 65001 >nul
REM ============================================================
REM  زر المالك: إيقاف مهووس
REM  يوقف حصراً عمليات بايثون التي يخص سطرُ أوامرها تطبيقَنا
REM  (streamlit run app.py / app.py) داخل مجلد هذا المشروع،
REM  ولا يلمس أي برنامج آخر على الجهاز.
REM ============================================================

REM ---- رفع صلاحيات (نسخة تشغيل-مهووس تعمل بصلاحيات مرتفعة) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title ايقاف مهووس

REM علم الإيقاف: يمنع حلقة إعادة التشغيل في تشغيل-مهووس.bat من إعادة فتح التطبيق
if exist "data" type nul > "data\.stop_requested"

set "MAHWOUS_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Get-Item -LiteralPath $env:MAHWOUS_ROOT).FullName.TrimEnd('\'); $cands=@(Get-CimInstance Win32_Process -Filter 'Name LIKE ''%%python%%''' | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'streamlit +run +app\.py' -or $_.CommandLine -match '(^|[ \\/])app\.py( |$)') }); $ids=@(); foreach($p in $cands){ $in=$false; if($p.ExecutablePath){ try{ if(((Get-Item -LiteralPath $p.ExecutablePath).FullName) -like ($root+'\*')){ $in=$true } }catch{} }; if(-not $in -and ($p.CommandLine -like ('*'+$root+'*'))){ $in=$true }; if($in){ $ids+=$p.ProcessId } }; if($ids.Count -gt 0){ $py=@(Get-CimInstance Win32_Process -Filter 'Name LIKE ''%%python%%'''); $added=$true; while($added){ $added=$false; foreach($q in $py){ if(($ids -contains $q.ParentProcessId) -and (-not ($ids -contains $q.ProcessId))){ $ids+=$q.ProcessId; $added=$true } } } }; foreach($i in $ids){ Stop-Process -Id $i -Force -ErrorAction SilentlyContinue }; exit 0"

echo.
echo تم إيقاف مهووس ✅
echo (اضغط أي زر لإغلاق هذه النافذة)
pause >nul
