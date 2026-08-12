# health_watchdog.ps1 — مراقب صحّة «مهووس»
# يفحص نبض الخادم كل تشغيل؛ إن كان متوقّفاً أو متجمّداً (عملية حيّة لكن لا تستجيب)
# يحرّر المنفذ ويعيد تشغيل مهمة MahwousServer. يُكمِّل حلقة إعادة التشغيل الداخلية
# (التي تلتقط الانهيار الصريح فقط) بالتقاط: التجمّد، وإغلاق النافذة/المهمة كلياً.
$ErrorActionPreference = 'SilentlyContinue'

$Url  = 'http://localhost:8502/_stcore/health'   # نقطة صحّة Streamlit الداخلية (بلا مصادقة)
$Task = 'MahwousServer'
$Log  = Join-Path $PSScriptRoot '..\data\watchdog.log'

function Write-Log([string]$Msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
    # تدوير بسيط: أبقِ آخر 500 سطر فقط كي لا ينمو السجلّ بلا حدّ
    $lines = @(Get-Content -Path $Log -ErrorAction SilentlyContinue)
    if ($lines.Count -gt 500) { $lines[-500..-1] | Set-Content -Path $Log -Encoding UTF8 }
}

# --- 1) نبض الخادم ---
$healthy = $false
try {
    $r = Invoke-WebRequest -Uri $Url -TimeoutSec 8 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $healthy = $true }
} catch { $healthy = $false }

if ($healthy) { exit 0 }   # كل شيء سليم — اخرج بصمت

# --- 2) الخادم لا يستجيب: حرّر المنفذ 8502 من أي عملية معلّقة ---
Write-Log "فحص الصحّة فشل — تحرير المنفذ 8502 وإعادة تشغيل $Task"
$conns = Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }

# --- 3) أعد تشغيل المهمة المجدولة (مصدر الحقيقة الوحيد للخادم) ---
schtasks /End /TN $Task | Out-Null
Start-Sleep -Seconds 3
schtasks /Run /TN $Task | Out-Null
Write-Log "صدر أمر إعادة التشغيل"
