"""services/top_competitor_service.py — الملف التقييمي لكل منتج + وسم «أكبر منافس».

**لكل منتج** (بصمة ``norm_name``) نحسب — من تقييم المنتج نفسه (``all_rating`` في
Algolia، لا تقييم المتجر العام):
  • ``total_rating_count`` — إجمالي تقييمات المنتج عبر كل المنافسين (وكيل الطلب/
    الحبّ السوقي — إشارة قرار الإضافة).
  • ``market_rating_avg``  — متوسط تقييم المنتج المرجّح بالعدد عبر المتاجر.
  • ``store_count``        — كم متجراً يبيعه (بتقييم).
  • أقوى بائع: ``competitor`` + ``rating_count`` + ``rating_avg`` (أعلى عدد تقييمات
    على هذا المنتج، تعادل ⇒ الأعلى متوسطاً).

يُبنى كاش ``product_top_competitor`` وقت الكشط (حيث لا أحد ينتظر) بنمط
``opportunity_scores``: DROP+CREATE+INSERT ذرّي داخل معاملة (تبديل آمن للمخطّط)،
خطأ⇒0، والقراءة مفهرسة على ``norm_name`` فلا رياضيات على مسار فتح الصفحة. أي
غياب بيانات ⇒ لا وسم (تدهور لطيف). القاعدة الحاكمة (كاش لا أرشيف): DROP/INSERT
مسموح على هذا الجدول وحده.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_TABLE = "product_top_competitor"

# لكل norm_name: إجمالي التقييمات + المتوسط المرجّح + عدد المتاجر (من التجميع)،
# مضمومة إلى أقوى بائع (أعلى عدد تقييمات، ثم أعلى متوسط). منتج بلا أي تقييم
# يُستبعَد (كل الأصفار سواء، لا معنى للترتيب).
_REBUILD_SQL = """
SELECT agg.norm_name,
       top.competitor, top.rating_count, top.rating_avg,
       agg.total_rc, agg.market_avg, agg.store_count
FROM (
    SELECT norm_name,
           SUM(rc) AS total_rc,
           SUM(rc * ra) / SUM(rc) AS market_avg,
           COUNT(*) AS store_count
    FROM (
        SELECT norm_name,
               COALESCE(rating_count, 0) AS rc,
               COALESCE(rating_avg, 0.0) AS ra
        FROM competitor_products_store
        WHERE norm_name != '' AND COALESCE(rating_count, 0) > 0
    )
    GROUP BY norm_name
) agg
JOIN (
    SELECT norm_name, competitor, rating_count, rating_avg FROM (
        SELECT norm_name, competitor,
               COALESCE(rating_count, 0) AS rating_count,
               COALESCE(rating_avg, 0.0) AS rating_avg,
               ROW_NUMBER() OVER (
                   PARTITION BY norm_name
                   ORDER BY COALESCE(rating_count, 0) DESC, COALESCE(rating_avg, 0.0) DESC
               ) AS rn
        FROM competitor_products_store
        WHERE norm_name != '' AND COALESCE(rating_count, 0) > 0
    ) WHERE rn = 1
) top ON top.norm_name = agg.norm_name
"""


def rebuild_top_competitor(db_path: Optional[str] = None) -> int:
    """يعيد بناء ``product_top_competitor`` (كاش مشتق) وقت الكشط؛ ذرّي؛ خطأ⇒0."""
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    try:
        ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(_REBUILD_SQL).fetchall()
            payload = [
                (
                    str(r[0]), str(r[1] or ""), int(r[2] or 0), float(r[3] or 0.0),
                    int(r[4] or 0), float(r[5] or 0.0), int(r[6] or 0), ts,
                )
                for r in rows
            ]
            # تبديل مخطّط ذرّي: DROP+CREATE+فهرسة+إدراج داخل معاملة واحدة صريحة (BEGIN)؛
            # فشل ⇒ تراجع كامل فيبقى الجدول القديم سليماً.
            # BEGIN صريح ضروري: على py3.14 لا تنضمّ DDL للمعاملة ضمنياً فتُفقَد الذرّية.
            with conn:
                conn.execute("BEGIN")
                conn.execute(f"DROP TABLE IF EXISTS {_TABLE}")
                conn.execute(
                    f"CREATE TABLE {_TABLE} ("
                    "norm_name TEXT PRIMARY KEY, competitor TEXT, "
                    "rating_count INTEGER, rating_avg REAL, "
                    "total_rating_count INTEGER, market_rating_avg REAL, "
                    "store_count INTEGER, rebuilt_at TEXT)"
                )
                # لا فهرس إضافي على norm_name — هو PRIMARY KEY أعلاه فعلياً
                # (فهرس ضمني تلقائي)، وكان idx_topcomp_nn مكرّراً عديم الفائدة.
                conn.executemany(
                    f"INSERT INTO {_TABLE} (norm_name, competitor, rating_count, rating_avg, "
                    "total_rating_count, market_rating_avg, store_count, rebuilt_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    payload,
                )
            return len(payload)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("rebuild_top_competitor فشل: %s", exc)
        return 0


def top_competitor_map(
    norm_names: Optional[Iterable[str]] = None, db_path: Optional[str] = None,
) -> dict[str, dict]:
    """قراءة مفهرسة: ``{norm_name: {...ملف تقييمي كامل...}}``.

    دفعة واحدة للصفحة (لا استعلام لكل صف). آمنة تماماً: أي خطأ/غياب جدول أو عمود
    (كاش قديم) ⇒ ``{}`` فلا تكسر أي صفحة. ``norm_names`` اختياري لتحديد النطاق.
    """
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                f"SELECT norm_name, competitor, rating_count, rating_avg, "
                "total_rating_count, market_rating_avg, store_count "
                f"FROM {_TABLE}"
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    wanted = set(norm_names) if norm_names is not None else None
    out: dict[str, dict] = {}
    for nn, comp, rc, ra, trc, mavg, sc in rows:
        if wanted is not None and nn not in wanted:
            continue
        out[nn] = {
            "competitor": comp,
            "rating_count": int(rc or 0),
            "rating_avg": float(ra or 0.0),
            "total_rating_count": int(trc or 0),
            "market_rating_avg": float(mavg or 0.0),
            "store_count": int(sc or 0),
        }
    return out


# ── كاش عملية + وسم بالاسم (لإعادة الاستخدام عبر كل صفحات البطاقات) ──────
_PROC_CACHE: dict = {}
_PROC_LOADED = False


def cached_map(force: bool = False) -> dict:
    """خريطة أكبر منافس مُخبّأة على مستوى العملية (تُحمّل مرّة، ~106م.ث للكل).

    آمنة تماماً: أي خطأ/غياب جدول ⇒ ``{}`` فلا تكسر أي صفحة.
    """
    global _PROC_LOADED
    if _PROC_LOADED and not force:
        return _PROC_CACHE
    try:
        _PROC_CACHE.clear()
        _PROC_CACHE.update(top_competitor_map())
        _PROC_LOADED = True
    except Exception:
        return {}
    return _PROC_CACHE


def badge_for_name(name: str) -> str:
    """وسم الملف التقييمي لمنتج بالاسم (يُطبَّع ويُبحَث في الكاش). فارغ آمن.

    يُستخدم في بطاقات المفقودات/الأقسام السعرية/جديد المنافسين — مفتاح موحّد =
    اسم منتج المنافس المطبَّع (يطابق ``competitor_products_store.norm_name``).
    """
    m = cached_map()
    if not m:
        return ""
    try:
        from engines.mahally_scraper import MahallyScraper
        return top_competitor_badge(m.get(MahallyScraper.normalize(str(name or ""))))
    except Exception:
        return ""


def top_competitor_badge(info: Optional[dict]) -> str:
    """نص وسم الملف التقييمي للمنتج (عرض فقط). فارغ آمن عند غياب البيانات.

    يقود بإشارة القرار **لكل منتج** (إجمالي تقييمات المنتج بالسوق + متوسطه)، ثم
    أقوى بائع. مثال: «⭐ 629 تقييم بالسوق · ★4.5 · الأقوى: سارا ميكب (626)».
    """
    if not info:
        return ""
    total = int(info.get("total_rating_count", 0) or 0)
    if total <= 0:
        return ""
    mavg = float(info.get("market_rating_avg", 0.0) or 0.0)
    star = f" · ★{mavg:.1f}" if mavg > 0 else ""
    comp = str(info.get("competitor", "") or "").strip()
    top_rc = int(info.get("rating_count", 0) or 0)
    strongest = f" · الأقوى: {comp} ({top_rc:,})" if comp and top_rc > 0 else ""
    return f"⭐ {total:,} تقييم بالسوق{star}{strongest}"


def rating_stars_html(info: Optional[dict]) -> str:
    """وسم HTML بارز للملف التقييمي (عرض فقط؛ نفس حارس ``top_competitor_badge``).

    يرسم 5 نجوم ذهبية مملوءة حتى تقريب المتوسط، مع الإجمالي وأقوى بائع. فارغ آمن.
    """
    if not info:
        return ""
    total = int(info.get("total_rating_count", 0) or 0)
    if total <= 0:
        return ""
    avg = float(info.get("market_rating_avg", 0.0) or 0.0)
    full = max(0, min(5, int(round(avg))))
    stars = "★" * full + "☆" * (5 - full)
    avg_txt = (f'<span style="color:#d4af37;font-weight:700">{avg:.1f}</span>'
               if avg > 0 else "")
    comp = str(info.get("competitor", "") or "").strip()
    top_rc = int(info.get("rating_count", 0) or 0)
    strongest = (f'<span style="opacity:.7">· الأقوى: {comp} ({top_rc:,})</span>'
                 if comp and top_rc > 0 else "")
    return (
        '<div dir="rtl" style="display:inline-flex;align-items:center;gap:8px;'
        'padding:5px 12px;border-radius:12px;background:rgba(212,175,55,.12);'
        'border:1px solid rgba(212,175,55,.35);font-size:.95rem;line-height:1.4">'
        f'<span style="color:#d4af37;letter-spacing:2px;font-size:1.05rem">{stars}</span>'
        f'{avg_txt}'
        f'<span style="opacity:.9">· {total:,} تقييم بالسوق</span>'
        f'{strongest}</div>'
    )


def badge_html_for_name(name: str) -> str:
    """نسخة HTML بارزة من ``badge_for_name`` (نفس البحث المطبَّع). فارغ آمن."""
    m = cached_map()
    if not m:
        return ""
    try:
        from engines.mahally_scraper import MahallyScraper
        return rating_stars_html(m.get(MahallyScraper.normalize(str(name or ""))))
    except Exception:
        return ""
