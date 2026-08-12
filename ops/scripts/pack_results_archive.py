"""يبني أرشيف نتائج جديداً من أرشيف منشور + لقطة الواجهة النهائية.

**لا تُلحق بـ``zipfile`` في وضع ``'a'``:** الأرشيف المنشور كُتب على ويندوز فأسماء
مدخلاته تحمل فواصل عكسية (``data\\pricing_v18.db``). وضعُ الإلحاق يُعيد كتابة الفهرس
المركزي بأسماء مطبَّعة إلى ``/`` بينما تبقى ترويسات الملفات المحلية بالفواصل العكسية،
فيصير الأرشيف غير مقروء: ``BadZipFile: File name in directory … and header … differ``.
مُختبَر فعلياً — الإلحاق أنتج أرشيفاً يبدو سليماً ويفشل عند أول قراءة.

لذلك تُنسخ المدخلات مدخلاً مدخلاً بأسماء POSIX نظيفة. يكلّف هذا إعادة ضغط، لكنه
ينتج أرشيفاً صحيحاً ويُنظّف الفواصل العكسية من المصدر نهائياً. القراءة بالتدفّق
(قطع 1م.ب) فلا تُحمَّل قاعدة 910م.ب في الذاكرة. ولا يُقرأ من القاعدة الحيّة إطلاقاً.

لماذا تُشحن اللقطة أصلاً: ``pricing_cache.json`` يُحفَظ في
``bootstrap.run_pricing_analysis`` **قبل** أن يطبّق ``analysis_runner`` الإنقاذَ
الحتمي، فإعادة بناء اللقطة من الكاشات تضع الصفوف المُنقَذة في «مستبعد» بدل «تحت
المراجعة». وإعادة تشغيل الإنقاذ داخل الحاوية غير واردة (526 ثانية، ومجمّع 300 ألف صف).

الاستعمال:

    python ops/scripts/pack_results_archive.py \
        --archive tozyw-results-complete.zip \
        --snapshot <مجلد data>/ui_session \
        --out tozyw-results-complete-v2.zip
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path


def snapshot_summary(snapshot: Path) -> dict[str, int]:
    """عدد صفوف كل قسم داخل اللقطة — يُطبع كي يرى المالك ما يشحنه فعلاً."""
    meta = json.loads((snapshot / "_meta.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for key, filename in (meta.get("frames") or {}).items():
        if not key.startswith("sections."):
            continue
        payload = json.loads((snapshot / str(filename)).read_text(encoding="utf-8"))
        counts[key[len("sections."):]] = len(payload.get("index") or [])
    return counts


def validate(snapshot: Path) -> None:
    meta_path = snapshot / "_meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"لا يوجد _meta.json في {snapshot}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    frames = meta.get("frames") or {}
    if not frames:
        raise SystemExit("اللقطة بلا إطارات — لن تُشحن")
    missing = [n for n in frames.values() if not (snapshot / str(n)).is_file()]
    if missing:
        raise SystemExit(f"إطارات مفقودة تسمّيها _meta.json: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="الأرشيف المنشور")
    parser.add_argument("--snapshot", required=True, type=Path, help="مجلد ui_session")
    parser.add_argument("--out", required=True, type=Path, help="الأرشيف الجديد")
    args = parser.parse_args()

    validate(args.snapshot)
    counts = snapshot_summary(args.snapshot)
    print("أقسام اللقطة التي ستُشحن:")
    for name, rows in sorted(counts.items()):
        print(f"  {name:<18} {rows:>7,}")
    pricing = {k: v for k, v in counts.items() if k != "missing_review"}
    print(f"  {'مجموع أقسام التسعير':<18} {sum(pricing.values()):>7,}")

    if args.out.exists():
        raise SystemExit(f"الوجهة موجودة مسبقاً، لن أستبدلها: {args.out}")

    print(f"\nبناء {args.out.name} من {args.archive.name} …")
    copied = shipped = 0
    with zipfile.ZipFile(args.archive) as src, \
            zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        seen: set[str] = set()
        for info in src.infolist():
            if info.is_dir():
                continue
            arcname = info.filename.replace("\\", "/")
            if "/ui_session/" in f"/{arcname}":
                continue  # لقطة قديمة داخل المصدر: تُستبدل بالمُمرَّرة
            if arcname in seen:
                raise SystemExit(f"مدخل مكرّر في المصدر: {arcname}")
            seen.add(arcname)
            with src.open(info) as reader, dst.open(arcname, "w") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            copied += 1
            print(f"  نُسخ {arcname} ({info.file_size:,} بايت)")

        for item in sorted(args.snapshot.iterdir()):
            if not item.is_file():
                continue
            arcname = f"data/ui_session/{item.name}"
            if arcname in seen:
                raise SystemExit(f"تعارض اسم: {arcname}")
            with item.open("rb") as reader, dst.open(arcname, "w") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            shipped += 1

    # فتحُ كل مدخل وقراءةُ أوّله: هذا وحده يكشف تعارض الترويسة مع الفهرس المركزي
    # الذي ينتجه وضع الإلحاق. أرخص كثيراً من testzip() على أرشيف بغيغابايت.
    with zipfile.ZipFile(args.out) as check:
        for name in check.namelist():
            with check.open(name) as probe:
                probe.read(1024)

    size = args.out.stat().st_size
    print(f"\nنُسخ {copied} ملفاً، وأُضيف {shipped} ملف لقطة. "
          f"الحجم النهائي: {size:,} بايت")
    print("\nالخطوة التالية: ارفعه أصلاً في إصدار GitHub **منشور** بوسم جديد، ثم غيّر")
    print("TOZYW_RESULTS_REVISION إلى الوسم الجديد و TOZYW_RESULTS_IMPORT_URL إلى رابطه.")


if __name__ == "__main__":
    sys.exit(main())
