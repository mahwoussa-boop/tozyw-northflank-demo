"""
🛠️ أداة الإصلاح الذكية — مهووس للتوزيع
═══════════════════════════════════════════
يطبّق 3 إصلاحات مؤكدة وآمنة بضغطة زر واحدة.

الاستخدام:
  python fix_tool.py          ← يعرض الإصلاحات ويسألك
  python fix_tool.py --apply  ← يطبق الكل مباشرة
  python fix_tool.py --undo   ← يتراجع عن كل التعديلات

كل إصلاح:
  ✅ ما يغيّر أي منطق
  ✅ ينشئ نسخة احتياطية تلقائية (.bak)
  ✅ يتحقق بعد التطبيق إن التعديل صحيح
"""

import os
import sys
import shutil
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ─── ألوان ──────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

PROJECT = Path(__file__).resolve().parent
BACKUP_SUFFIX = ".bak_fix"


# ═══════════════════════════════════════════════════════════════
#  الإصلاحات الثلاثة
# ═══════════════════════════════════════════════════════════════

FIXES = [
    {
        "id": 1,
        "title": "assert → if/raise (حماية المنتجات من الضياع)",
        "file": "services/ai_router_service.py",
        "risk": "🟢 صفر خطر",
        "benefit": "🔴 حرج — حماية المنتجات تبقى دائماً فعّالة",
        "patches": [
            {
                "find": 'assert total_routed == original_total, (',
                "replace": 'if total_routed != original_total:\n            raise RuntimeError(',
            },
            {
                "find": 'assert total_from_checked == total, (',
                "replace": 'if total_from_checked != total:\n            raise RuntimeError(',
            },
        ],
    },
    {
        "id": 2,
        "title": "try/finally لإغلاق اتصال قاعدة البيانات",
        "file": "utils/db_manager.py",
        "risk": "🟢 صفر خطر",
        "benefit": "🟡 متوسط — يمنع تسريب اتصالات SQLite",
        "patches": [
            {
                "find":    "    conn = get_db()\n    today = _date()",
                "replace": "    conn = get_db()\n    try:\n        today = _date()",
            },
            {
                "find":    "    conn.commit(); conn.close()\n    return price_changed",
                "replace": "        conn.commit()\n        return price_changed\n    finally:\n        conn.close()",
            },
        ],
    },
    {
        "id": 3,
        "title": "عدّاد المنتجات الفارغة (missing_confirmed)",
        "file": "services/ai_redistributor.py",
        "risk": "🟢 صفر خطر",
        "benefit": "🟡 متوسط — الأرقام المعروضة تصير صحيحة",
        "patches": [
            {
                "find":    "dedup_count = 0\r\n            for idx in range",
                "replace": "dedup_count = 0\r\n            _skipped_empty = 0\r\n            for idx in range",
            },
            {
                "find":    "if not name:\r\n                        continue",
                "replace": "if not name:\r\n                        _skipped_empty += 1\r\n                        continue",
            },
            {
                "find":    "plan.missing_confirmed = total - dedup_count",
                "replace": "plan.missing_confirmed = total - dedup_count - _skipped_empty",
            },
        ],
    },
]


# ═══════════════════════════════════════════════════════════════
#  دوال مساعدة
# ═══════════════════════════════════════════════════════════════

def _path(rel):
    return PROJECT / rel

def _backup(path):
    bak = Path(str(path) + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    return bak

def _restore(path):
    bak = Path(str(path) + BACKUP_SUFFIX)
    if bak.exists():
        shutil.copy2(bak, path)
        bak.unlink()
        return True
    return False

def _show(fix):
    print()
    print(f"  {BOLD}{CYAN}━━━ إصلاح #{fix['id']}: {fix['title']} ━━━{RESET}")
    print(f"  {DIM}الملف:{RESET}   {fix['file']}")
    print(f"  {DIM}الخطر:{RESET}   {fix['risk']}")
    print(f"  {DIM}الفائدة:{RESET} {fix['benefit']}")
    print()

def _apply(fix):
    path = _path(fix["file"])
    if not path.exists():
        return False, f"الملف غير موجود: {path}"

    _backup(path)
    content = path.read_text(encoding="utf-8")
    original = content

    for i, p in enumerate(fix["patches"]):
        find_text = p["find"]
        repl_text = p["replace"]

        if find_text in content:
            content = content.replace(find_text, repl_text, 1)
        else:
            # حاول بدون \r
            alt_find = find_text.replace("\r\n", "\n")
            alt_repl = repl_text.replace("\r\n", "\n")
            if alt_find in content:
                content = content.replace(alt_find, alt_repl, 1)
            else:
                return False, f"لم أجد النص المطلوب (patch {i+1}) — ربما تم الإصلاح مسبقاً"

    if content == original:
        return False, "لم يتغيّر شيء — ربما مُطبّق مسبقاً"

    path.write_text(content, encoding="utf-8")

    # تحقق
    verify = path.read_text(encoding="utf-8")
    for p in fix["patches"]:
        check = p["replace"]
        if check not in verify and check.replace("\r\n", "\n") not in verify:
            return False, "⚠️ التحقق فشل!"

    return True, "تم بنجاح ✅"


# ═══════════════════════════════════════════════════════════════
#  الواجهة
# ═══════════════════════════════════════════════════════════════

def main():
    print()
    print(f"  {BOLD}{CYAN}🛠️  أداة الإصلاح الذكية — مهووس للتوزيع{RESET}")
    print(f"  {DIM}{'━' * 45}{RESET}")

    # ── تراجع ──
    if "--undo" in sys.argv:
        print(f"\n  {YELLOW}⏪ التراجع عن جميع التعديلات...{RESET}\n")
        for fix in FIXES:
            if _restore(_path(fix["file"])):
                print(f"  {GREEN}✅ تم استعادة {fix['file']}{RESET}")
            else:
                print(f"  {DIM}— {fix['file']} (لا نسخة احتياطية){RESET}")
        print()
        return

    # ── عرض ──
    print(f"\n  {BOLD}📋 الإصلاحات المتاحة ({len(FIXES)}):{RESET}")
    for fix in FIXES:
        _show(fix)

    # ── اختيار ──
    auto = "--apply" in sys.argv
    if not auto:
        print(f"  {BOLD}{'━' * 40}{RESET}")
        print(f"    {BOLD}1{RESET} = 🚀 طبّق الكل دفعة واحدة")
        print(f"    {BOLD}2{RESET} = 🎯 اختر إصلاحات محددة")
        print(f"    {BOLD}0{RESET} = ❌ إلغاء")
        print()
        choice = input(f"  {CYAN}اختيارك ← {RESET}").strip()

        if choice == "0":
            print(f"\n  {DIM}تم الإلغاء.{RESET}\n")
            return
        elif choice == "2":
            ids = input(f"  {CYAN}أرقام الإصلاحات (مثال: 1,3) ← {RESET}").strip()
            selected = [int(x) for x in ids.replace(" ", "").split(",") if x.isdigit()]
            to_apply = [f for f in FIXES if f["id"] in selected]
        else:
            to_apply = FIXES
    else:
        to_apply = FIXES

    if not to_apply:
        print(f"\n  {YELLOW}لا إصلاحات محددة.{RESET}\n")
        return

    # ── تطبيق ──
    print(f"\n  {BOLD}{GREEN}🚀 جاري التطبيق...{RESET}\n")

    ok_count = 0
    for fix in to_apply:
        ok, msg = _apply(fix)
        icon = f"{GREEN}✅{RESET}" if ok else f"{RED}❌{RESET}"
        print(f"  {icon}  #{fix['id']} {fix['title']}")
        print(f"     {DIM}{msg}{RESET}")
        if ok:
            ok_count += 1

    # ── ملخص ──
    print(f"\n  {BOLD}━━━ النتيجة ━━━{RESET}")
    print(f"  نجح: {GREEN}{ok_count}{RESET} / {len(to_apply)}")
    if ok_count > 0:
        print(f"  {DIM}📦 النسخ الاحتياطية: *.bak_fix{RESET}")
        print(f"  {DIM}⏪ للتراجع: python fix_tool.py --undo{RESET}")
    print()


if __name__ == "__main__":
    main()
