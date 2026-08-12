# -*- coding: utf-8 -*-
"""scripts/report_automation_health.py — فحص صحّة أتمتة «مهووس» (قراءة فقط).

**لماذا هذا الملف موجود:** رمز الخروج ``0x0`` من مهمة مجدولة **لا يعني** أنها
أنجزت عملها. عطبان حقيقيان أثبتا ذلك:

* 2026-07-23 — ``MahwousNightlyScrape`` كانت تُنهي بـ``0x1`` كل ليلة لأن مسار
  الإجراء في تعريفها **مشوّه الترميز** (ملف غير موجود)، فبقيت البيانات بائتة
  خمسة أيام والنضارة تعتمد على الزرّ اليدوي وحده.
* 2026-07-25 — ``MahwousVisionVerify`` تفشل كل ساعتين لأن ``run_vision_verify.bat``
  **حُذف من القرص** بينما بقي تعريف المهمة يشير إليه.

كلاهما اكتُشف بتدقيق يدوي لا بإنذار. لذلك يفحص هذا التقرير أمرين معاً لكل
أتمتة: (1) هل ملف الإجراء **موجود فعلاً** على القرص، و(2) هل تركت المهمة
**أثراً حقيقياً** في البيانات (صفوف كشط · نسخة اليوم · إعادة بناء الرادار · نبض
الخادم). لا يكتب في أي قاعدة بيانات ولا يعدّل أي مهمة — تشخيص صرف.

التشغيل:  ``.venv\\Scripts\\python.exe -X utf8 scripts\\report_automation_health.py``
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "pricing_v18.db"
BACKUPS_DIR = ROOT / "data" / "backups"

OK, WARN, FAIL = "✅", "⚠️", "❌"

# رموز خروج مقبولة من مجدول Windows: نجاح · قيد التشغيل الآن · لم تُشغَّل بعد
CODE_RUNNING = 0x41301
CODE_NEVER_RUN = 0x41303
ACCEPTED_CODES = {0, CODE_RUNNING, CODE_NEVER_RUN}

# عتبات «أنجزت عملها فعلاً»
MIN_SCRAPE_RUNS_24H = 40   # الجولة الليلية تغطي ~74 متجراً؛ ما دون ذلك جولة مبتورة
MAX_AGE_HOURS = 26.0       # مهمة يومية يجب أن تترك أثراً خلال آخر 26 ساعة


def parse_ts(value: str | None) -> datetime | None:
    """يحلّل طوابع المستودع الزمنية: ``YYYY-MM-DD HH:MM:SS`` (محلّي ساذج) و ISO بمنطقة زمنية."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def hours_since(value, now_local: datetime, now_utc: datetime) -> float | None:
    """عمر الطابع بالساعات. الطوابع الواعية بالمنطقة تُقارن بـUTC، والساذجة بالتوقيت المحلّي."""
    moment = value if isinstance(value, datetime) else parse_ts(value)
    if moment is None:
        return None
    if moment.tzinfo is not None:
        return (now_utc - moment).total_seconds() / 3600.0
    return (now_local - moment).total_seconds() / 3600.0


def judge_task(name: str, state: str, last_result: int | None,
               exec_path: str | None, exec_exists: bool | None,
               effect: dict | None = None) -> dict:
    """الحكم النقيّ على أتمتة واحدة. ``effect`` = {'ok': bool, 'text': str} أو None.

    الترتيب مقصود: ملف الإجراء المفقود أخطر من رمز الخروج، لأنه **سبب** الفشل
    المتكرّر ويظلّ صامتاً حين يبتلع ملف .bat الخطأ ويعيد 0.
    """
    lines: list[str] = []
    status = OK
    disabled = str(state).lower() == "disabled"

    if exec_path and exec_exists is False:
        status = FAIL
        lines.append(f"ملف الإجراء غير موجود على القرص: {exec_path}")

    if disabled:
        lines.append("المهمة معطَّلة — لن تعمل تلقائياً")

    if last_result is not None and last_result not in ACCEPTED_CODES:
        status = FAIL
        lines.append(f"آخر خروج 0x{last_result:X} (فشل)")

    if effect is not None:
        lines.append(effect.get("text", ""))
        if not effect.get("ok", False):
            status = FAIL

    # مهمة معطَّلة لا تعمل أصلاً، فلا يمكن أن «تفشل»: قرار مالك مركون لا عطب
    # يحتاج تدخّلاً. تُخفَّض إلى تنبيه كي لا تُغرق الأعطالُ الحقيقيةَ في الضجيج.
    if disabled and status == FAIL:
        status = WARN

    if status == OK and not lines:
        lines.append("تعمل وتترك أثراً سليماً")
    return {"name": name, "status": status, "lines": [ln for ln in lines if ln]}


def collect_tasks() -> list[dict]:
    """يقرأ مهام ``Mahwous*`` من مجدول Windows عبر PowerShell (قراءة فقط)."""
    # فرض UTF-8 على مخرَج PowerShell: مسارات المهام عربية، وترميز الطرفية
    # الافتراضي (صفحة الرموز) يعيدها بايتات لا تُفكّ فينهار التحليل.
    ps = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-ScheduledTask | Where-Object {$_.TaskName -like 'Mahwous*'} | "
        "ForEach-Object { $i=Get-ScheduledTaskInfo -TaskName $_.TaskName; "
        "$e=$_.Actions[0].Execute; [pscustomobject]@{name=$_.TaskName; state=[string]$_.State; "
        "result=$i.LastTaskResult; last=[string]$i.LastRunTime; exec=$e; "
        # إجراء بلا مجلد (مثل 'powershell') أمرٌ يُحلّ عبر PATH لا مسار ملف:
        # فحصه بـTest-Path وحده يعطي إيجابية كاذبة، فنسأل Get-Command أولاً.
        "exists=$(if(-not $e){$true}elseif(Split-Path $e -Parent){Test-Path $e}"
        "else{[bool](Get-Command $e -ErrorAction SilentlyContinue)})} } | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout or ""
        out = out.strip()
        data = json.loads(out) if out else []
        return data if isinstance(data, list) else [data]
    except Exception as exc:  # بيئة بلا PowerShell أو مجدول — لا نكسر التقرير
        print(f"{WARN} تعذّر قراءة مهام المجدول: {exc}")
        return []


def collect_effects(now_local: datetime, now_utc: datetime) -> dict:
    """يقيس الأثر الحقيقي لكل أتمتة من القاعدة الحيّة ومن القرص (``mode=ro``)."""
    effects: dict[str, dict] = {}

    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            runs, latest = conn.execute(
                "SELECT COUNT(*), MAX(run_at) FROM scrape_runs "
                "WHERE julianday('now') - julianday(run_at) <= 1"
            ).fetchone()
            effects["MahwousNightlyScrape"] = {
                "ok": (runs or 0) >= MIN_SCRAPE_RUNS_24H,
                "text": f"جولات كشط خلال 24 ساعة: {runs or 0} متجر (آخرها {latest or '—'})",
            }

            rebuilt = conn.execute("SELECT MAX(rebuilt_at) FROM opportunity_scores").fetchone()[0]
            age = hours_since(rebuilt, now_local, now_utc)
            effects["MahwousRadarRebuild"] = {
                "ok": age is not None and age <= MAX_AGE_HOURS,
                "text": f"آخر إعادة بناء للرادار: {rebuilt or '—'}"
                        + (f" (منذ {age:.1f} ساعة)" if age is not None else ""),
            }
            conn.close()
        except Exception as exc:
            effects["MahwousNightlyScrape"] = {"ok": False, "text": f"تعذّرت قراءة القاعدة: {exc}"}

    dated = sorted(p for p in BACKUPS_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*") if p.is_dir())
    if dated:
        newest = dated[-1]
        size_mb = sum(f.stat().st_size for f in newest.rglob("*") if f.is_file()) / 1024 / 1024
        age = hours_since(datetime.fromtimestamp(newest.stat().st_mtime), now_local, now_utc)
        effects["MahwousDailyBackup"] = {
            "ok": age is not None and age <= MAX_AGE_HOURS and size_mb > 1,
            "text": f"أحدث نسخة: {newest.name} ({size_mb:,.0f} م.ب"
                    + (f"، منذ {age:.1f} ساعة)" if age is not None else ")"),
        }
    else:
        effects["MahwousDailyBackup"] = {"ok": False, "text": "لا توجد أي نسخة احتياطية مؤرَّخة"}

    try:
        from urllib.request import urlopen
        with urlopen("http://localhost:8502/_stcore/health", timeout=8) as resp:
            alive = resp.status == 200
    except Exception:
        alive = False
    effects["MahwousServer"] = {
        "ok": alive,
        "text": "نبض الخادم على 8502: " + ("يستجيب" if alive else "لا يستجيب"),
    }
    return effects


def main() -> int:
    now_local, now_utc = datetime.now(), datetime.now(timezone.utc)
    print("=" * 62)
    print(f"  فحص صحّة أتمتة «مهووس» — {now_local:%Y-%m-%d %H:%M}")
    print("=" * 62)

    effects = collect_effects(now_local, now_utc)
    tasks = collect_tasks()
    seen, results = set(), []

    for task in tasks:
        name = task.get("name", "؟")
        seen.add(name)
        results.append(judge_task(
            name, task.get("state", ""), task.get("result"),
            task.get("exec"), task.get("exists"), effects.get(name),
        ))

    # أثر بلا مهمة مقابلة = أتمتة مفقودة من المجدول أصلاً
    for name, effect in effects.items():
        if name not in seen:
            results.append(judge_task(name, "missing", None, None, None, effect))
            results[-1]["lines"].insert(0, "لا توجد مهمة بهذا الاسم في المجدول")
            results[-1]["status"] = FAIL

    for item in sorted(results, key=lambda r: (r["status"] != FAIL, r["name"])):
        print(f"\n{item['status']}  {item['name']}")
        for line in item["lines"]:
            print(f"      • {line}")

    failures = [r for r in results if r["status"] == FAIL]
    warnings = [r for r in results if r["status"] == WARN]
    print("\n" + "-" * 62)
    if failures:
        print(f"{FAIL} أعطال تحتاج تدخّلاً: {len(failures)} — " + "، ".join(r["name"] for r in failures))
    if warnings:
        print(f"{WARN} تنبيهات: {len(warnings)} — " + "، ".join(r["name"] for r in warnings))
    if not failures and not warnings:
        print(f"{OK} كل الأتمتة سليمة وتترك أثراً حقيقياً.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
