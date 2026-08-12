# -*- coding: utf-8 -*-
"""services/send_floor_guard.py — أرضية «خطأ الكشط» على مسار الإرسال (وحدة أ2).

قبل كل إرسال فعلي: أي سعر أقل من ``FLOOR_RATIO`` (افتراضياً 50%) من وسيط سوقه
(``opportunity_scores.price_median``) يُحجز للمراجعة بدل الإرسال — درع ضد سعر
مكشوط فاسد يصل Make. لا حجب بلا يقين سعري: عنصر بلا صفّ سوق مطابق
(``no_market_row``) أو بلا اسم/سعر صالح **يمرّ دائماً**.

مبادئ صارمة (مطابقة لـ ``services/pricing_shadow.py`` — نفس مصدر القراءة):
  • القراءة ``mode=ro`` فقط — صفر كتابة، صفر حذف، صفر تعديل سعر.
  • ``FLOOR_RATIO`` ثابت مسمّى — تصفيره (``0.0``) يعطّل الحجب فوراً بلا حذف كود.
  • fail-open: أي خطأ قاعدة/اتصال ⇒ الكل يمرّ (عطل تقني لا يوقف الإرسال أبداً).
  • لا رفض صامت: كل عنصر محجوز يحمل ``blocked_reason`` نصياً ظاهراً.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

# نسبة الأرضية — سعر إرسال أقل منها من وسيط السوق يُحجز للمراجعة.
# صفّرها (0.0) لتعطيل الحجب فوراً دون حذف أي كود (مسار تراجع فوري).
FLOOR_RATIO = 0.50


def split_below_floor(
    items: Optional[list], db_path: Optional[str] = None,
) -> tuple[list, list]:
    """يقسم عناصر الإرسال إلى (مسموح، محجوز). لا يرفع استثناء أبداً."""
    if not FLOOR_RATIO:
        return list(items or []), []
    try:
        from services.pricing_shadow import _extract_name_price
        import services.pricing_shadow as _ps
        from utils.db_manager import _normalize_for_store

        path = db_path or _ps._default_db()
        ro = sqlite3.connect(
            "file:" + str(path).replace("\\", "/") + "?mode=ro", uri=True
        )
        try:
            allowed: list[Any] = []
            blocked: list[Any] = []
            for item in items or []:
                if not isinstance(item, dict):
                    allowed.append(item)
                    continue
                name, price = _extract_name_price(item)
                if not name or not price or price <= 0:
                    allowed.append(item)
                    continue
                nn = _normalize_for_store(name)
                hit = ro.execute(
                    "SELECT price_median FROM opportunity_scores"
                    " WHERE norm_name=? LIMIT 1", (nn,)
                ).fetchone()
                median = hit[0] if hit else None
                if median and median > 0 and price < FLOOR_RATIO * median:
                    reason = (
                        f"سعر الإرسال {price:g} ر.س أقل من "
                        f"{int(FLOOR_RATIO * 100)}% من وسيط السوق {median:g} ر.س "
                        "— محتمل خطأ كشط، يحتاج مراجعة"
                    )
                    blocked.append({**item, "blocked_reason": reason})
                else:
                    allowed.append(item)
            return allowed, blocked
        finally:
            ro.close()
    except Exception:
        return list(items or []), []
