# -*- coding: utf-8 -*-
"""scripts/make_portable_zip.py — نسخة محمولة تعمل على جهاز آخر (Windows).

يحزم المصدر + البيانات في ملف zip واحد يُرسَل لجهاز آخر: يفكّه المستقبِل ثم
يشغّل «تشغيل-مهووس.bat» (يثبّت بايثون + المكتبات ذاتياً ويفتح المنفذ). القواعد
تُنسخ عبر sqlite backup API (نسخة متسقة حتى والتطبيق يعمل). يُستثنى الثقيل عديم
الفائدة: .venv (يُعاد بناؤه)، .git، data/backups، الكاشات، __pycache__.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# اسم لاتيني: أأمن للنقل (بريد/USB) من الاسم العربي الذي قد يتشوّه.
OUT = ROOT.parent / f"mahwous-portable-{date.today().isoformat()}.zip"

# مجلدات تُستثنى كلياً (نسبةً لجذر المشروع).
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache",
             os.path.join("data", "backups"), os.path.join("data", "reconcile")}
# لاحقات/أنماط تُستثنى.
SKIP_SUFFIX = (".pyc", ".pyo", ".db-shm", ".db-wal", ".zip", ".log")
# كاشات قابلة لإعادة التوليد (تُستثنى لتخفيف الحجم).
SKIP_NAMES = {"pricing_cache.json", "missing_cache.json", "match_cache_v22.db",
              ".stop_requested"}
DB_FILES = {"pricing_v18.db", "perfume_pricing.db"}


def _is_secret(name: str) -> bool:
    """ملفات أسرار حيّة — لا تخرج أبداً في حزمة منقولة (توكن سلة بصلاحية الكتابة،
    مفاتيح Gemini/OpenRouter/Algolia). يُبقى قالبا `.example` لأنهما آمنان ومفيدان."""
    if name == "secrets.toml":
        return True
    return name.startswith(".env") and not name.endswith(".example")


def _skip(rel: str) -> bool:
    parts = Path(rel).parts
    for d in SKIP_DIRS:
        dp = Path(d).parts
        if parts[:len(dp)] == dp:
            return True
    name = Path(rel).name
    if _is_secret(name):
        return True
    return name in SKIP_NAMES or rel.endswith(SKIP_SUFFIX)


def _consistent_db_copy(src: Path, tmp: Path) -> None:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp))
    with dst:
        con.backup(dst)
    con.close(); dst.close()


def main() -> int:
    files, dbs, total = [], [], 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT)
        # قصّ المجلدات المستثناة مبكراً (أسرع + يتجنّب المشي فيها).
        dirnames[:] = [d for d in dirnames
                       if not _skip(os.path.join(rel_dir, d) if rel_dir != "." else d)]
        for fn in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, fn)) if rel_dir != "." else fn
            if _skip(rel):
                continue
            src = Path(dirpath) / fn
            if fn in DB_FILES:
                dbs.append((src, rel))
            else:
                files.append((src, rel))
                total += src.stat().st_size

    print(f"ملفات عادية: {len(files)} · قواعد: {len(dbs)} · الحجم الخام ~{total/1024**2:,.0f}MB")
    # شبكة أمان تفشل-مغلقة: لو تسلّل ملف سرّ رغم _skip، أحبِط بدل شحن التوكنات الحيّة.
    leaked = [rel for _, rel in files if _is_secret(Path(rel).name)]
    if leaked:
        print(f"⛔ إحباط: ملفات أسرار في الحزمة — {leaked}", file=sys.stderr)
        return 2
    tmp_dbs = []
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, rel in files:
            z.write(src, rel)
        for src, rel in dbs:  # نسخة متسقة عبر backup API ثم حزمها
            tmp = ROOT / f"._pkg_{src.name}"
            print(f"  نسخ متسق: {rel} ({src.stat().st_size/1024**2:,.0f}MB)…")
            _consistent_db_copy(src, tmp)
            z.write(tmp, rel)
            tmp_dbs.append(tmp)
    for tmp in tmp_dbs:
        tmp.unlink(missing_ok=True)

    size = OUT.stat().st_size
    print(f"\n✅ أُنشئت النسخة المحمولة:\n   {OUT}\n   الحجم: {size/1024**2:,.1f}MB")
    print("🔒 استُثنيت ملفات الأسرار (.env / secrets.toml) — لا توكنات في الحزمة.")
    print("على الجهاز الآخر: فُكّ الضغط → شغّل «تشغيل-مهووس.bat» (يثبّت كل شيء ويفتح المنفذ).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
