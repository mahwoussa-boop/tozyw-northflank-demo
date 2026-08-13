"""services/competitor_availability.py — توفّر المنافس وقِدَم بياناته (مصدر واحد).

نُقلت الدالتان من ``ui/components/comparison_card.py`` كما هما (نفس الاستعلامين
ونفس الكاش) لتصير الحقيقة **واحدة** تقرأها الواجهة *والمحرّك* معاً: المحرّك كان
لا يرى التوفّر إطلاقاً (صفر ورود لكلمة ``availability`` في ``engines/engine.py``)
فيختار مرجعاً سعرياً قد يكون نافداً، بينما البطاقة تعرض «🔴 نفذت» لنفس المنتج.

**لماذا كاش على مستوى العملية لا عمود في الإطار:** ``bootstrap.load_competitor_dfs``
يُسقط ``availability``/``updated_at`` عمداً (تعليق الأداء هناك) وإضافتهما تعني
نموّاً في ذاكرة **كل جلسة** — والجلسة تكلّف ~219 م.ب أصلاً. هذان القاموسان
مبنيّان مرّة واحدة لكل عملية ومفتاحهما ``mtime`` القاعدة، فيتجدّدان تلقائياً بعد
كل كشطة ولا يتضاعفان بعدد الجلسات. والفهرسان ``idx_cps_url_updated`` و
``idx_cps_avail_updated`` موجودان أصلاً لخدمة هذين الاستعلامين بالذات.

كل شيء هنا **fail-open**: أي عطل (قاعدة غائبة/عمود ناقص) يعيد قيمة «غير معروف»
لا حجباً — لا يجوز أن يُسكِت عطلٌ تقنيٌّ منتجاً عن المالك.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# وسوم التوفّر — تُقارَن بالثابت لا بالنصّ الحرفي (م4 يعتمد عليها).
AVAIL_IN = "متوفر"
AVAIL_OUT = "نافد"
AVAIL_UNKNOWN = "غير معروف"

_AVAIL_CACHE: dict[str, Any] = {"sig": None, "oos": frozenset()}
_AGE_CACHE: dict[str, Any] = {"sig": None, "ages": {}}


def _db_signature() -> tuple[str, Optional[float]]:
    """مسار قاعدة المنافسين وزمن تعديلها (مفتاح الكاش)."""
    from conf.constants import COMPETITOR_DB_PATH

    db = str(COMPETITOR_DB_PATH)
    try:
        return db, os.path.getmtime(db)
    except OSError:
        return db, None


def oos_links() -> frozenset:
    """روابط منتجات المنافسين النافِذة (``availability=0``). آمنة تماماً وسريعة.

    تُخزَّن بمفتاح ``getmtime`` للقاعدة: استدعاء O(1) بعد أول بناء، وإعادة بناء
    تلقائية بعد أي كشطة تُعدّل القاعدة. قاعدة/عمود مفقود ⇒ مجموعة فارغة.
    """
    db, sig = _db_signature()
    if sig is None:
        return frozenset()
    if _AVAIL_CACHE["sig"] == sig:
        return _AVAIL_CACHE["oos"]

    import sqlite3

    oos: set[str] = set()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute(
                "PRAGMA table_info(competitor_products_store)")]
            if "availability" in cols:
                rows = con.execute(
                    "SELECT product_url FROM competitor_products_store "
                    "WHERE availability=0 AND product_url IS NOT NULL "
                    "AND product_url<>''"
                ).fetchall()
                oos = {str(r[0]).strip() for r in rows if r[0]}
        finally:
            con.close()
    except Exception:
        oos = set()
    _AVAIL_CACHE["sig"] = sig
    _AVAIL_CACHE["oos"] = frozenset(oos)
    return _AVAIL_CACHE["oos"]


def data_ages() -> dict[str, float]:
    """خريطة رابط_منتج ← قِدَم البيانات بالأيام (نفس صيغة ``opportunity_service``:
    ``julianday('now') - julianday(updated_at)``). مخزَّنة بمفتاح ``mtime`` كـ``oos_links``."""
    db, sig = _db_signature()
    if sig is None:
        return {}
    if _AGE_CACHE["sig"] == sig:
        return _AGE_CACHE["ages"]

    import sqlite3

    ages: dict[str, float] = {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT product_url, MIN(julianday('now') - julianday(updated_at)) "
                "FROM competitor_products_store "
                "WHERE product_url IS NOT NULL AND product_url<>'' "
                "GROUP BY product_url"
            ).fetchall()
            ages = {str(r[0]).strip(): float(r[1]) for r in rows if r[0] and r[1] is not None}
        finally:
            con.close()
    except Exception:
        ages = {}
    _AGE_CACHE["sig"] = sig
    _AGE_CACHE["ages"] = ages
    return _AGE_CACHE["ages"]


def lookup(product_url: str) -> tuple[str, Optional[float]]:
    """(وسم التوفّر، قِدَم البيانات بالأيام) لرابط منتج منافس.

    رابط فارغ أو غير معروف للقاعدة ⇒ ``(AVAIL_UNKNOWN, None)`` — لا نَدَّعي
    توفّراً لم نقسه.
    """
    url = str(product_url or "").strip()
    if not url:
        return AVAIL_UNKNOWN, None
    age = data_ages().get(url)
    oos = oos_links()
    if not oos and age is None:
        return AVAIL_UNKNOWN, None
    label = AVAIL_OUT if url in oos else AVAIL_IN
    return label, age
