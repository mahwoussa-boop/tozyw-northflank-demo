# -*- coding: utf-8 -*-
"""services/send_band_guard.py — سياج نطاق v1 على مسار الإرسال (وحدة أ4).

فرض تدريجي (docs/خطة-التطوير-الشاملة.md #أ4): يُطبَّق فقط على أقسام
``ENFORCED_SECTIONS`` — المرحلة 1 (2026-07-22) فعَّلت «سعر أعلى» (``"raise"``)
وحده؛ المرحلة 2 (2026-07-22، بموافقة صريحة من المالك) أضافت «سعر أقل»
(``"lower"``) — القسم الوحيد الآخر الذي يرسل تغيير سعر فعلي لسلة. لكل عنصر
إرسال في قسم مُفعَّل: يُقارَن سعره بنطاق ±20% حول
``opportunity_scores.price_median`` عبر نفس حكم الظلّ ``pricing_shadow.verdict_for``
(لا إعادة اختراع). **خارج النطاق فقط (below_band/above_band) يُحجز للمراجعة** —
بلا صفّ سوق مطابق أو ببيانات قديمة (``no_market_row``/``stale_market``) **يمرّ
دائماً** (لا حجب بلا يقين سعري، نفس مبدأ ``send_floor_guard``؛ قياس أ1 الحيّ أظهر
أن ~47% من صفوف الظلّ بلا صفّ سوق مطابق — حجبها جميعاً كان سيوقف نصف القسم فعلياً).

مبادئ صارمة (مطابقة لـ send_floor_guard/pricing_shadow):
  • القراءة ``mode=ro`` فقط — صفر كتابة، صفر تعديل سعر.
  • ``ENFORCED_SECTIONS`` مجموعة مسمّاة — تفريغها (``set()``) يعطّل الحجب فوراً
    بلا حذف كود (يعيد الوضع لظلّ-فقط) — مسار تراجع فوري.
  • fail-open: أي خطأ قاعدة/اتصال ⇒ الكل يمرّ (عطل تقني لا يوقف الإرسال أبداً).
  • لا رفض صامت: كل عنصر محجوز يحمل ``blocked_reason`` نصياً ظاهراً.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

# الأقسام المفعَّل فيها فرض سياج v1 — مرحلتان مُفعَّلتان الآن (raise ثم lower).
# تفريغها (set()) يعيد الوضع لظلّ-فقط فوراً بلا حذف كود — مسار تراجع فوري.
ENFORCED_SECTIONS: set = {"raise", "lower"}
_OVERRIDE_REASON_KEY = "_band_override_reason"
_OVERRIDE_APPLIED_KEY = "_band_override_applied"
_OVERRIDE_GUARD_REASON_KEY = "_band_override_guard_reason"


def record_and_strip_band_overrides(items: Optional[list]) -> list:
    """يسجل تجاوزات النطاق ثم يزيل حقول التحكم قبل إرسال حمولة Make.

    لا يمكن للمُستدعي تمرير ``_band_override_reason`` وحده لتجاوز الحارس؛
    الحارس نفسه يضيف ``_band_override_applied`` فقط بعد تحقق النطاق.
    """
    cleaned: list = []
    for item in items or []:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        row = dict(item)
        reason = str(row.pop(_OVERRIDE_REASON_KEY, "") or "").strip()
        applied = bool(row.pop(_OVERRIDE_APPLIED_KEY, False))
        guard_reason = str(row.pop(_OVERRIDE_GUARD_REASON_KEY, "") or "")
        if applied and reason:
            try:
                from services.decision_journal import record_decision
                record_decision(
                    "band_override", str(row.get("name") or row.get("product_name") or ""),
                    str(row.get("section") or ""), chosen=row.get("price"),
                    extra={"reason": reason, "guard_reason": guard_reason,
                           "sku": str(row.get("NO") or row.get("product_id") or "")},
                )
            except Exception:
                # دفتر القرار دفاعي، ولا ينبغي أن يقلب إرسالاً مؤكداً إلى فشل.
                pass
        cleaned.append(row)
    return cleaned


def _reason_text(verdict: str, price: float, median: float) -> str:
    pct = f"{price / median * 100:.0f}%" if median else "?"
    if verdict == "below_band":
        return (
            f"سعر الإرسال {price:g} ر.س ({pct} من الوسيط) أقل من نطاق سياج v1 "
            f"(80%-120% من وسيط السوق {median:g} ر.س) — يحتاج تجاوزاً مسجَّلاً"
        )
    return (
        f"سعر الإرسال {price:g} ر.س ({pct} من الوسيط) أعلى من نطاق سياج v1 "
        f"(80%-120% من وسيط السوق {median:g} ر.س) — يحتاج تجاوزاً مسجَّلاً"
    )


def split_outside_band(
    items: Optional[list], db_path: Optional[str] = None,
) -> tuple[list, list]:
    """يقسم عناصر الإرسال إلى (مسموح، محجوز خارج نطاق v1).

    الفحص يقتصر على عناصر ``item["section"] in ENFORCED_SECTIONS``؛ عناصر
    الأقسام الأخرى تمرّ دوماً بلا فحص. لا يرفع استثناء أبداً.
    """
    if not ENFORCED_SECTIONS:
        return list(items or []), []
    try:
        from services.pricing_shadow import _default_db, _extract_name_price, verdict_for
        from utils.db_manager import _normalize_for_store

        path = db_path or _default_db()
        ro = sqlite3.connect(
            "file:" + str(path).replace("\\", "/") + "?mode=ro", uri=True
        )
        try:
            allowed: list[Any] = []
            held: list[Any] = []
            for item in items or []:
                if not isinstance(item, dict) or item.get("section") not in ENFORCED_SECTIONS:
                    allowed.append(item)
                    continue
                name, price = _extract_name_price(item)
                if not name or not price or price <= 0:
                    allowed.append(item)
                    continue
                nn = _normalize_for_store(name)
                hit = ro.execute(
                    "SELECT price_median, stale_reason FROM opportunity_scores"
                    " WHERE norm_name=? LIMIT 1", (nn,)
                ).fetchone()
                median, stale = (hit[0], hit[1]) if hit else (None, None)
                verdict = verdict_for(price, median, stale)
                if verdict in ("below_band", "above_band"):
                    guard_reason = _reason_text(verdict, price, median)
                    override_reason = str(item.get(_OVERRIDE_REASON_KEY, "") or "").strip()
                    if override_reason:
                        allowed.append({
                            **item,
                            _OVERRIDE_APPLIED_KEY: True,
                            _OVERRIDE_GUARD_REASON_KEY: guard_reason,
                        })
                    else:
                        held.append({**item, "blocked_reason": guard_reason})
                else:
                    allowed.append(item)
            return allowed, held
        finally:
            ro.close()
    except Exception:
        return list(items or []), []
