# restart_server.ps1 — إعادة تشغيل خادم «مهووس» بعد تحديث الكود
#
# لماذا هذا السكربت موجود (تشخيص 2026-07-25): Streamlit يعيد تنفيذ app.py عند كل
# تفاعل، لكن الوحدات المستوردة (ui/pages/*.py) تبقى في ذاكرة العملية كما حُمّلت
# أول مرّة، و`runOnSave` غير مضبوط (افتراضه false). فأي تعديل مدموج **لا يصل
# المالك حتى إعادة تشغيل الخادم**. ومراقب الصحّة لا يكفي: منطقه
# `if ($healthy) { exit 0 }` — يعيد التشغيل عند فشل النبض فقط لا عند تغيّر الكود.
#
# يختلف عن health_watchdog.ps1 في ثلاثة أمور مقصودة:
#   1) يعيد التشغيل **دائماً** لا عند الفشل فقط.
#   2) يمسح علم الإيقاف data/.stop_requested — يضعه «إيقاف-مهووس.bat» لمنع حلقة
#      إعادة التشغيل، ولو بقي لرفض المُشغّل العودة فيبدو كأن الإعادة فشلت.
#   3) **يتحقّق من النبض بعد الإعادة** ويُرجع رمز خروج صادقاً — لا يكتفي بإصدار
#      الأمر (نفس درس «فحص صحّة الأتمتة»: تحقّق من الأثر لا من رمز الخروج).
$ErrorActionPreference = 'SilentlyContinue'

$Url  = 'http://localhost:8502/_stcore/health'
$Task = 'MahwousServer'
$Root = Split-Path -Parent $PSScriptRoot
$Stop = Join-Path $Root 'data\.stop_requested'

function Test-Health {
    try { return (Invoke-WebRequest -Uri $Url -TimeoutSec 8 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}

function Get-Listener {
    return (Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1).OwningProcess
}

Write-Host ''
Write-Host '  إعادة تشغيل خادم مهووس (لتفعيل تحديثات الكود)...'

# **جوهر الصدق هنا:** نُسجّل هويّة العملية قبل الإعادة. فحص النبض وحده لا يكفي —
# لو فشل القتل (صلاحيات) لبقي الخادم **القديم** يردّ 200 فيبدو كأن الإعادة نجحت
# والكود القديم ما زال يعمل. حدث ذلك فعلاً في أول نسخة من هذا السكربت.
$oldPid = Get-Listener
Write-Host "  · الخادم الحالي: PID $oldPid"

if (Test-Path -LiteralPath $Stop) {
    Remove-Item -LiteralPath $Stop -Force
    Write-Host '  · مُسح علم الإيقاف (كان سيمنع عودة الخادم)'
}

# منع الصفحات المنبثقة (2026-07-25): مهمة MahwousServer تشغّل «تشغيل-مهووس.bat»
# نفسه، وفيه `start "" http://localhost:8502`. والمراقب يعيد التشغيل عند كل فشل
# نبض ⇒ كل مرّة تنبثق صفحة جديدة في وجه المالك (ستّ في ساعة). تمرير العلامة
# noweb يُسكت الفتح **للمهمة وحدها**؛ نقر المالك على الملف يبقى يفتح الصفحة.
# يُضبط من هنا لأن تعديل مهمة مرفوعة يلزمه رفع صلاحيات، وهذا السكربت يُشغَّل
# عبر «أعد-تشغيل-الخادم.bat» الذي يرفعها أصلاً.
$act = (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue).Actions[0]
if ($act -and $act.Arguments -notmatch 'noweb') {
    try {
        Set-ScheduledTask -TaskName $Task -ErrorAction Stop -Action (
            New-ScheduledTaskAction -Execute $act.Execute -Argument 'noweb'
        ) | Out-Null
        Write-Host '  · أُوقفت الصفحات المنبثقة (المهمة تمرّر noweb الآن)'
    } catch {
        Write-Host '  ⚠️ تعذّر ضبط noweb على المهمة — قد تظل صفحة تنبثق عند كل إعادة تشغيل.'
    }
}

$killFailed = $false
foreach ($c in (Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue)) {
    try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop }
    catch { $killFailed = $true }
}
if ($killFailed) {
    Write-Host ''
    Write-Host '  ❌ تعذّر إيقاف الخادم — صلاحيات غير كافية.'
    Write-Host '     الخادم يعمل بصلاحيات مرتفعة، فلا بدّ من تشغيل هذا الملف كمسؤول.'
    Write-Host '     شغّل «أعد-تشغيل-الخادم.bat» (يرفع الصلاحيات تلقائياً) لا هذا السكربت مباشرة.'
    exit 1
}
Write-Host '  · أُوقف الخادم وحُرِّر المنفذ 8502'

schtasks /End /TN $Task | Out-Null
Start-Sleep -Seconds 3
schtasks /Run /TN $Task | Out-Null
Write-Host '  · صدر أمر التشغيل — في انتظار خادم **جديد**...'

# الشرط مزدوج: نبض سليم **و** هويّة عملية مختلفة. الانتظار حتى 90 ثانية لأن
# البدء البارد يحمّل القاعدة والفهارس من القرص.
$ok = $false
$newPid = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 3
    $newPid = Get-Listener
    if ((Test-Health) -and $newPid -and ($newPid -ne $oldPid)) { $ok = $true; break }
}

Write-Host ''
if ($ok) {
    Write-Host "  ✅ خادم جديد يعمل ويستجيب: PID $oldPid ← $newPid  (بعد ~$(($i + 1) * 3) ثانية)"
    Write-Host '     التحديثات صارت حيّة. افتحه: http://localhost:8502'
    exit 0
}
if ((Test-Health) -and $newPid -eq $oldPid) {
    Write-Host "  ❌ الخادم يردّ لكنه **نفس العملية القديمة** (PID $oldPid) — لم تُعَد."
    Write-Host '     التحديثات لم تصل. شغّل «أعد-تشغيل-الخادم.bat» كمسؤول.'
    exit 1
}
Write-Host '  ❌ الخادم لم يستجب خلال 90 ثانية.'
Write-Host '     جرّب «تشغيل-مهووس.bat» يدوياً، وراجع data\watchdog.log'
exit 1
