# -*- coding: utf-8 -*-
"""scripts/run_nightly_scrape.py — كشط ليلي آلي لكل المنافسين (بديل الزر اليدوي).

المشكلة (كشف جلسة 2026-07-18): زر "🚀 كشط الكل" في واجهة الكشط هو المسار الوحيد
لتحديث بيانات المنافسين — لا أتمتة توجد له. تقرير نضارة البيانات كشف توقّف الكشط
فعلياً 4 أيام (آخر صف في ``scrape_runs``: 2026-07-14)، فصار حارس النضارة
(``STALE_AFTER_DAYS=3``) يحجب/يعلّم كل اقتراحات الرادار كقديمة دون علم المالك.

آمن تماماً:
  • يستدعي ``ScraperService.scrape_all()`` نفسها التي يستدعيها الزر اليدوي — لا منطق
    كشط جديد، صفر تكرار.
  • قفل ``.scrape.lock`` الذاتي في ``scrape_all`` يمنع التصادم مع كشط يدوي متزامن
    (لو فتح المالك التطبيق وضغط الزر وقت التشغيل المجدول) — هذا السكربت يلتقط
    ``PricingError`` الناتج بهدوء ويخرج بنجاح (لا فشل مزعج، الجولة التالية تكفي).
  • ``scrape_all`` تُعيد بناء كل الكواش المشتقة تلقائياً في نهاية الجولة (الفرص/أكبر
    منافس/تقييم المتاجر/الأولوية/اكتشاف متاجر) — مهمة ``MahwousRadarRebuild``
    اللاحقة (04:30) تبقى شبكة أمان إضافية غير ضارة، لا اعتماداً حرجاً.
  • لا يكتب في أي جدول إلا عبر ``ScraperService`` نفسها (نفس مسار الزر اليدوي حرفياً).

يُسجَّل الناتج (توقيت البداية/النهاية، عدد المتاجر، نتيجة كل متجر، المدة الكلية)
لتشخيص أي فشل صامت لاحقاً.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# طباعة آمنة الترميز على console الويندوز (نفس فخّ rebuild_radar.py).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.exceptions import PricingError  # noqa: E402
from services.scraper_service import ScraperService  # noqa: E402


def main() -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"=== كشط ليلي آلي — بدأ {stamp} ===")

    scraper = ScraperService()
    t0 = time.time()
    try:
        results = scraper.scrape_all()
    except PricingError as exc:
        # الأرجح: كشط يدوي متزامن يحمل القفل — لا فشل، الجولة التالية تكفي.
        print(f"⏳ تم التخطي: {exc}")
        return 0
    except Exception as exc:
        print(f"❌ فشل الكشط الآلي: {exc}")
        return 1

    dt = time.time() - t0
    ok = sum(1 for v in results.values() if v > 0)
    failed = sum(1 for v in results.values() if v < 0)
    zero = sum(1 for v in results.values() if v == 0)
    total_products = sum(v for v in results.values() if v > 0)

    print(f"\n--- خلاصة ({dt:.0f}s = {dt/60:.1f} دقيقة) ---")
    print(f"  متاجر: {len(results)} | نجح: {ok} | صفر منتج: {zero} | فشل: {failed}")
    print(f"  إجمالي منتجات مكشوطة: {total_products}")
    if failed:
        failed_names = [k for k, v in results.items() if v < 0]
        print(f"  المتاجر الفاشلة: {', '.join(failed_names[:20])}")

    end_stamp = datetime.now().isoformat(timespec="seconds")
    print(f"=== انتهى {end_stamp} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
