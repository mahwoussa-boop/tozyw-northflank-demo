"""services/pricing_shadow.py — السياج الظِّلّي (تدوين فقط — لا يغيّر سعراً أبداً).

وضع الظلّ لسياسة v1: عند كل إرسال إلى Make يُقارن السعر المُرسَل فعلاً بالسعر
المقترَح المُسبق الحساب في ``opportunity_scores`` (المبني بسياسة v1: أدنى متوفر −1
داخل ±20% من وسيط السوق + حارس الطزاجة)، ويُدوَّن الفرق في جدول
``pricing_guard_shadow`` — **بلا أي تعديل على السعر ولا منع للإرسال**.

مبادئ صارمة:
  • صفر رياضيات وقت الإرسال — قراءة ``opportunity_scores`` المفهرس (idx_oppscore_nn) فقط.
  • القراءة ``mode=ro``؛ الكتابة INSERT صرف في جدول الظلّ (لا DELETE/UPDATE أبداً).
  • التطبيع **الخفيف المخزّن** (``_normalize_for_store``) حصراً — لا ``normalize_name``
    الثقيل (مفاتيح ``opportunity_scores`` مخزّنة بالخفيف؛ الثقيل يُفشل كل بحث).
  • الدالة كلها خلف try/except — أي خطأ ⇒ 0 صفوف وتحذير، **لا يمنع الإرسال أبداً**.

verdict ∈ {ok, below_band, above_band, stale_market, no_market_row}.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("PricingShadow")

# حاجز النطاق حول وسيط السوق (نفس BAND_PCT في opportunity_service — سياسة v1).
BAND_PCT = 0.20

def _default_db() -> str:
    """مسار القاعدة الافتراضي — دالة مستقلة كي تُعزل في الاختبارات (conftest)."""
    from utils.data_paths import get_data_db_path
    return get_data_db_path("pricing_v18.db")


_DDL = """CREATE TABLE IF NOT EXISTS pricing_guard_shadow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, path TEXT, sku TEXT, name TEXT, norm_name TEXT,
    sent_price REAL, suggested_price REAL, price_median REAL,
    instock_price_min REAL, data_age_days REAL, verdict TEXT
)"""


def verdict_for(
    sent_price: float,
    price_median: Optional[float],
    stale_reason: Optional[str] = None,
) -> str:
    """حكم الظلّ (دالة خالصة): أين يقع السعر المُرسَل من نطاق ±20% حول الوسيط.
    بيانات قديمة ⇒ ``stale_market``؛ بلا وسيط صالح ⇒ ``no_market_row``."""
    if stale_reason == "stale":
        return "stale_market"
    if not price_median or price_median <= 0:
        return "no_market_row"
    if sent_price < (1 - BAND_PCT) * price_median:
        return "below_band"
    if sent_price > (1 + BAND_PCT) * price_median:
        return "above_band"
    return "ok"


def _extract_name_price(item: dict) -> tuple[str, Optional[float]]:
    """اسم المنتج (المنافس أولاً — مفتاح السوق) والسعر المُرسَل من عنصر الحمولة."""
    name = str(
        item.get("comp_name") or item.get("منتج_المنافس")
        or item.get("name") or item.get("أسم المنتج") or ""
    ).strip()
    raw = item.get("price", item.get("سعر المنتج"))
    try:
        price = float(raw)
    except (TypeError, ValueError):
        price = None
    return name, price


def record_shadow(items: Any, path: str, db_path: Optional[str] = None) -> int:
    """يدوّن قرار الظلّ لكل عنصر إرسال صالح. يعيد عدد الصفوف المُدوَّنة.

    آمن كلياً: أي خطأ (قاعدة غائبة/جدول غائب/عنصر فاسد) ⇒ يتجاوز أو يعيد 0
    بتحذير — **لا يرفع استثناء ولا يمنع الإرسال ولا يغيّر السعر أبداً**.
    """
    try:
        if db_path is None:
            db_path = _default_db()
        from utils.db_manager import _normalize_for_store  # التطبيع الخفيف المخزّن

        rows: list[tuple] = []
        ro = sqlite3.connect(
            "file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True
        )
        try:
            for item in items or []:
                if not isinstance(item, dict) or item.get("section") == "test":
                    continue
                name, price = _extract_name_price(item)
                if not name or not price or price <= 0:
                    continue
                nn = _normalize_for_store(name)
                sku = str(
                    item.get("NO") or item.get("product_id")
                    or item.get("رمز المنتج sku") or ""
                )
                hit = ro.execute(
                    "SELECT suggested_price, price_median, instock_price_min,"
                    " data_age_days, stale_reason FROM opportunity_scores"
                    " WHERE norm_name=? LIMIT 1", (nn,)
                ).fetchone()
                if hit is None:
                    rows.append((path, sku, name, nn, price,
                                 None, None, None, None, "no_market_row"))
                else:
                    sugg, med, inmin, age, stale = hit
                    rows.append((path, sku, name, nn, price, sugg, med, inmin, age,
                                 verdict_for(price, med, stale)))
        finally:
            ro.close()

        if not rows:
            return 0
        writer = sqlite3.connect(str(db_path), timeout=30)
        try:
            writer.execute(_DDL)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            writer.executemany(
                "INSERT INTO pricing_guard_shadow (ts, path, sku, name, norm_name,"
                " sent_price, suggested_price, price_median, instock_price_min,"
                " data_age_days, verdict) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(ts, *row) for row in rows])
            writer.commit()
        finally:
            writer.close()
        return len(rows)
    except Exception as exc:
        logger.warning("السياج الظِّلّي تعذّر (لا يمنع الإرسال): %s", exc)
        return 0
