# -*- coding: utf-8 -*-
"""services/send_quality_guard.py — بوّابة أهلية الإرسال: ثقة · توفّر · حداثة (م4).

الحارسان القائمان يحرسان **السعر** (``send_floor_guard`` أرضية 50% من الوسيط،
``send_band_guard`` نطاق ±20%). الناقص كان **جودة الدليل** الذي بُني عليه القرار:
مطابقةٌ ضعيفة، أو سوقٌ كل عروضه نافدة، أو بياناتٌ قديمة. هذا الحارس يسدّ ذلك
بنفس نمط شقيقيه — لا نمط جديد ولا تعطيل لهما.

الإشارات الثلاث ومصادرها:
  • **الثقة**  : ``match_score`` الموجود أصلاً في حمولة Make (يضعه
    ``export_service._add_context`` من ``نسبة_التطابق``) — لا نلمس ``_add_context``
    فهي ``#PRESERVED_LOGIC``.
  • **التوفّر**: ``opportunity_scores.instock_price_min IS NULL`` ⇒ لا عرض متاح
    في سوق هذا المنتج إطلاقاً (3,726 من 54,626 صفّاً = 6.8%).
  • **الحداثة**: ``opportunity_scores.data_age_days`` مقابل
    ``opportunity_service.STALE_AFTER_DAYS`` — نفس عتبة النظام، لا رقم جديد.

مبادئ صارمة (مطابقة لـ ``send_floor_guard``/``send_band_guard``):
  • القراءة ``mode=ro`` فقط — صفر كتابة، صفر تعديل سعر.
  • ``ENFORCED_SECTIONS`` مجموعة مسمّاة — تفريغها (``set()``) يعطّل الحجب فوراً
    بلا حذف كود — مسار تراجع فوري. وكذلك ``MIN_MATCH_SCORE = 0`` و
    ``REQUIRE_IN_STOCK = False`` يعطّلان فحصيهما وحدهما.
  • fail-open: أي خطأ قاعدة/صفّ سوق غائب ⇒ يمرّ (عطل تقني لا يوقف الإرسال).
  • لا رفض صامت: كل محجوز يحمل ``blocked_reason`` نصياً عربياً ظاهراً، ويعود
    في طابور مستقل قابل للمراجعة — الحجب لا يصير فقدان رؤية.

⚠️ ``MIN_MATCH_SCORE`` قرار مالك لا قرار مبرمج. الأثر المقيس على أقسام القرار
(7,030 صفّاً في اللقطة الحيّة): 70%⇒517 · 75%⇒733 · **80%⇒995 (14.15%)** ·
85%⇒1,244 · 90%⇒1,695. القيمة أدناه هي نقطة البدء المعروضة على المالك؛ تعديلها
سطرٌ واحد.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

# الأقسام التي ترسل تغيير سعر فعلي لسلة — نفس مجموعة send_band_guard.
ENFORCED_SECTIONS: set = {"raise", "lower"}

# عتبة الثقة الدنيا (0-100). صفّرها لتعطيل فحص الثقة وحده.
MIN_MATCH_SCORE: float = 80.0

# هل يُشترط وجود عرض منافس **متاح** في السوق؟ False يعطّل فحص التوفّر وحده.
REQUIRE_IN_STOCK: bool = True


def _stale_after_days() -> float:
    """عتبة التقادم — من ``opportunity_service`` كي لا يوجد رقمان متنافران."""
    try:
        from services.opportunity_service import STALE_AFTER_DAYS
        return float(STALE_AFTER_DAYS)
    except Exception:
        return 3.0


def _match_score_of(item: dict) -> Optional[float]:
    """ثقة المطابقة من الحمولة. غيابها ⇒ ``None`` (يمرّ — لا حجب بلا معلومة)."""
    for key in ("match_score", "نسبة_التطابق"):
        if key in item:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                return None
    return None


def split_low_quality(
    items: Optional[list], db_path: Optional[str] = None,
) -> tuple[list, list]:
    """يقسم عناصر الإرسال إلى (مسموح، محجوز لضعف الدليل).

    الفحص يقتصر على ``item["section"] in ENFORCED_SECTIONS``؛ غيرها يمرّ بلا فحص.
    لا يرفع استثناء أبداً.
    """
    if not ENFORCED_SECTIONS:
        return list(items or []), []
    try:
        from services.pricing_shadow import _default_db, _extract_name_price
        from utils.db_manager import _normalize_for_store

        max_age = _stale_after_days()
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

                # ── 1) الثقة (من الحمولة نفسها، بلا قاعدة) ──
                score = _match_score_of(item)
                if MIN_MATCH_SCORE > 0 and score is not None and score < MIN_MATCH_SCORE:
                    held.append({**item, "blocked_reason": (
                        f"ثقة المطابقة {score:.0f}% أقل من الحد الأدنى "
                        f"{MIN_MATCH_SCORE:.0f}% — الدليل ضعيف، يحتاج مراجعة"
                    )})
                    continue

                # ── 2+3) التوفّر والحداثة (صفّ السوق) ──
                name, _price = _extract_name_price(item)
                if not name:
                    allowed.append(item)
                    continue
                hit = ro.execute(
                    "SELECT instock_price_min, data_age_days FROM opportunity_scores"
                    " WHERE norm_name=? LIMIT 1", (_normalize_for_store(name),)
                ).fetchone()
                if not hit:
                    allowed.append(item)  # لا صفّ سوق ⇒ لا حجب بلا يقين
                    continue
                instock_min, age = hit[0], hit[1]

                if REQUIRE_IN_STOCK and instock_min is None:
                    held.append({**item, "blocked_reason": (
                        "كل عروض المنافسين لهذا المنتج نافدة — لا سعر متاح "
                        "يُقارَن به، يحتاج مراجعة"
                    )})
                    continue

                if age is not None and float(age) > max_age:
                    held.append({**item, "blocked_reason": (
                        f"بيانات المنافسين عمرها {float(age):.0f} يوم "
                        f"(الحد {max_age:.0f}) — قديمة، تحتاج كشطة قبل الإرسال"
                    )})
                    continue

                allowed.append(item)
            return allowed, held
        finally:
            ro.close()
    except Exception:
        return list(items or []), []
