"""core/pricing_guard.py — سياج التسعير (سياسة v1 الحرفية).

⚠️ هذه سياسة v1 الحرفية — أي تعديل هنا قرار مالي يوقّعه المالك.

دالة نقية واحدة بلا أي استيراد من ``engines/`` أو ``services/`` (مكتبة
``statistics`` القياسية فقط). لا I/O ولا شبكة ولا قاعدة بيانات — قابلة للاختبار
مباشرةً. **غير موصولة بأي مسار إرسال** — دالة حساب صرفة خلف موافقة المالك.

مرجع السياسة (CLAUDE.md بند 11):
  • مرجع الأقل المتوفر: أدنى سعر متاح − 1.0.
  • حاجز ±20% حول وسيط السوق: النطاق = [0.8×الوسيط، 1.2×الوسيط].
  • أرضية التكلفة وحدها (لا ذروة زمنية، لا تعديل مخزون، لا تعظيم إيراد).
"""
from __future__ import annotations

import statistics
from typing import Any, Optional


def _coerce_price(value: Any) -> Optional[float]:
    """يحوّل قيمة السعر إلى float موجب، أو None إن كانت فاسدة (None/0/سالب/غير رقمية)."""
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return p


def apply_policy_v1(
    competitor_prices: list,
    cost_price: float = 0.0,
    our_price: Optional[float] = None,
) -> dict:
    """يطبّق سياسة تسعير مهووس v1 على أسعار المنافسين ويعيد قراراً موثّقاً.

    ⚠️ هذه سياسة v1 الحرفية — أي تعديل هنا قرار مالي يوقّعه المالك.

    Args:
        competitor_prices: قائمة عناصر (dict) لكلٍّ ``price`` و``available``.
            المتاح = العناصر التي ``available`` صادق و``price`` رقم > 0
            (تُتجاهَل قيم 0/سالبة/None/غير الرقمية).
        cost_price: تكلفتنا. > 0 ⇒ أرضية التكلفة مُفعّلة؛ == 0 ⇒ معطّلة
            (تُسجَّل في ``floor_active`` لا تُخفى).
        our_price: سعرنا الحالي — يُقبَل للتوافق ولا تستخدمه سياسة v1
            (مرجعها الأقل المتوفر، لا سعرنا).

    Returns: dict يحوي **دائماً** المفاتيح:
        ok, price (مقرّب لخانتين أو None), verdict, floor_active,
        n_available, n_total, min_available, median, band_low, band_high.

    verdict ∈ {no_available_market, ok, clamped_to_band_low,
               clamped_to_band_high, raised_to_cost_floor, blocked_cost_above_band}.
    """
    items = competitor_prices or []
    n_total = len(items)
    floor_active = bool(cost_price and cost_price > 0)

    available: list[float] = []
    for item in items:
        try:
            raw = item.get("price")
            avail = item.get("available")
        except AttributeError:
            continue
        if not avail:
            continue
        p = _coerce_price(raw)
        if p is not None:
            available.append(p)

    n_available = len(available)

    if n_available == 0:
        return {
            "ok": False,
            "price": None,
            "verdict": "no_available_market",
            "floor_active": floor_active,
            "n_available": 0,
            "n_total": n_total,
            "min_available": None,
            "median": None,
            "band_low": None,
            "band_high": None,
        }

    min_available = min(available)
    median = float(statistics.median(available))
    band_low = 0.8 * median
    band_high = 1.2 * median

    base = min_available - 1.0

    # ── حاجز النطاق حول الوسيط ──
    if base < band_low:
        price = band_low
        verdict = "clamped_to_band_low"
    elif base > band_high:
        # احتياطي نظري: مع (أدنى − 1) لا يتجاوز base السقف أبداً،
        # لأن band_high = 1.2×الوسيط ≥ 1.2×الأدنى > الأدنى > الأدنى−1.
        price = band_high
        verdict = "clamped_to_band_high"
    else:
        price = base
        verdict = "ok"

    # ── أرضية التكلفة وحدها ──
    if floor_active and price < cost_price:
        if cost_price <= band_high:
            price = float(cost_price)
            verdict = "raised_to_cost_floor"
        else:
            return {
                "ok": False,
                "price": None,
                "verdict": "blocked_cost_above_band",
                "floor_active": True,
                "n_available": n_available,
                "n_total": n_total,
                "min_available": min_available,
                "median": median,
                "band_low": band_low,
                "band_high": band_high,
            }

    return {
        "ok": True,
        "price": round(price, 2),
        "verdict": verdict,
        "floor_active": floor_active,
        "n_available": n_available,
        "n_total": n_total,
        "min_available": min_available,
        "median": median,
        "band_low": band_low,
        "band_high": band_high,
    }
