"""scripts/perf_baseline.py — قياس أداء الأساس (قراءة فقط) لجلسة تخفيف مهووس.

يقيس بـ``time.perf_counter`` على القاعدة/اللقطة الحقيقية:
  (أ) ``quality_report_data(our_catalog)``              — تحميل لوحة التحكم.
  (ب) ``out_of_stock(limit=10000)`` + عدّادا (OOS/جديد) — صفحة «نفذت».
  (ج) ``check_and_prepare_brands(القسم المؤكَّد)``      — قسم المفقودات المفلتر.

قراءة فقط تماماً: يقرأ ``ui_session.json`` ويشغّل ``SELECT`` ودوالّ خالصة —
لا كتابة صفوف ولا إرسال ولا لمس للشبكة. يُطبَع كل زمن بالمللي ثانية (وسيط ٣ قياسات).

الاستعمال:  ``.venv/Scripts/python.exe -m scripts.perf_baseline``
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # ضمان طباعة عربية/رموز سليمة على كونسول Windows (cp1256)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from conf.constants import DATA_DIR


def _time_ms(fn: Callable[[], Any], repeat: int = 3) -> tuple[float, Any]:
    """يقيس زمن ``fn`` (ms) عائداً بالوسيط ونتيجة آخر تشغيل. لا يُعدّل شيئاً."""
    times: list[float] = []
    result: Any = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(times), result


def main() -> None:
    # اللقطة صارت مجلّداً (صيغة 3) — جسر التوافق يعيد نفس البنية الخام القديمة.
    from ui.state_manager import load_snapshot_raw

    snap = load_snapshot_raw()
    our_catalog = snap.get("our_catalog")
    missing_df = snap.get("missing_df")

    print("═" * 64)
    print("  قياس أساس الأداء — قراءة فقط (وسيط ٣ قياسات، بالمللي ثانية)")
    print("═" * 64)

    # ── (أ) تقرير جودة البيانات (لوحة التحكم) ──────────────────────
    from ui.pages.dashboard import quality_report_data

    n_cat = 0 if our_catalog is None else len(our_catalog)
    quality_report_data(our_catalog)  # تسخين (استيراد المُقيّم لمرة واحدة)
    ms_a, _ = _time_ms(lambda: quality_report_data(our_catalog))
    print(f"(أ) quality_report_data(our_catalog[{n_cat:,} صف])   : {ms_a:9.1f} ms")

    # ── (ب) صفحة «نفذت» ───────────────────────────────────────────
    from services.scraper_service import ScraperService

    svc = ScraperService()
    svc.out_of_stock(limit=1)  # تسخين (فحص المخطّط يُخزَّن بعدها)
    ms_oos, rows = _time_ms(lambda: svc.out_of_stock(limit=10000))
    print(f"(ب1) out_of_stock(limit=10000) [رجّع {len(rows):,}]  : {ms_oos:9.1f} ms")
    ms_coos, n_coos = _time_ms(lambda: len(svc.out_of_stock(limit=10000)))
    print(f"(ب2) عدّاد OOS = len(out_of_stock(10000)) = {n_coos:>6,} : {ms_coos:9.1f} ms")
    ms_cnew, n_cnew = _time_ms(lambda: len(svc.new_products(limit=10000)))
    print(f"(ب3) عدّاد جديد = len(new_products(10000)) = {n_cnew:>6,}: {ms_cnew:9.1f} ms")

    # ── (ج) فحص ماركات القسم المؤكَّد (green) من المفقودات ─────────
    from services.brand_manager import check_and_prepare_brands
    from ui.pages.missing import filter_by_confidence

    green = filter_by_confidence(missing_df, "مؤكد")
    records = [] if green is None else green.to_dict("records")
    ms_c, (rows_c, names_c) = _time_ms(
        lambda: check_and_prepare_brands(records)
    )
    print(
        f"(ج) check_and_prepare_brands(المؤكَّد[{len(records):,} صف]) "
        f"[ناقص {len(names_c):,}] : {ms_c:9.1f} ms"
    )
    print("═" * 64)


if __name__ == "__main__":
    main()
