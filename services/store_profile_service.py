"""services/store_profile_service.py — تقييم المتجر لكل منافس (وحدة معزولة).

**معزولة تماماً عن كاشط المنتجات:** لا تمسّ ``_parse_hit`` ولا مسار حفظ المنتجات.
تجلب تقييم المتجر (``store_rating`` في Algolia — مستوى المتجر، لا المنتج) باستعلام
واحد خفيف لكل متجر (hit واحد بفلتر ``store_id`` المتوفّر في إعداد المنافس)، وتخزّنه
في جدول ``competitor_stores`` (upsert). يُشغَّل نهاية الكشط (حيث لا أحد ينتظر) أو
يدوياً. أي فشل شبكة/قاعدة ⇒ يتابع بلا كسر (تدهور لطيف: لا وسم متجر).

يكمل «التقييم لكل منتج» (top_competitor_service): المنتج من ``all_rating``، والمتجر
من ``store_rating`` — إشارتان متكاملتان لقرار الإضافة والثقة بالمصدر.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

_TABLE = "competitor_stores"


def fetch_store_rating(store_id: int) -> Optional[dict]:
    """استعلام Algolia واحد خفيف لتقييم المتجر. ``None`` عند أي خطأ/غياب.

    شبكة معزولة — تُحقَن بديلاً في الاختبار (لا اتصال حيّ).
    """
    if not store_id:
        return None
    try:
        import requests
        from engines.mahally_scraper import ALGOLIA_API_URL, ALGOLIA_HEADERS
        resp = requests.post(
            ALGOLIA_API_URL, headers=ALGOLIA_HEADERS,
            json={
                "query": "", "hitsPerPage": 1,
                "facetFilters": [f"store_id:{store_id}"],
                "attributesToRetrieve": ["store_rating", "store_trust_score", "store_name"],
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        hits = resp.json().get("hits", [])
        if not hits:
            return None
        sr = hits[0].get("store_rating") or {}
        if not isinstance(sr, dict):
            sr = {}
        return {
            "rating_avg": float(sr.get("average") or 0),
            "rating_count": int(sr.get("count") or 0),
            "trust_score": float(hits[0].get("store_trust_score") or 0),
        }
    except Exception as exc:  # pragma: no cover - مسار الشبكة
        logger.debug("fetch_store_rating(%s) فشل: %s", store_id, exc)
        return None


def refresh_store_profiles(
    competitors: Iterable[Any],
    fetch_fn: Callable[[int], Optional[dict]] = fetch_store_rating,
    db_path: Optional[str] = None,
) -> int:
    """يحدّث ``competitor_stores`` لكل منافس له ``mahally_store_id``.

    upsert بالاسم؛ فشل متجر واحد لا يوقف الباقي؛ يُرجع عدد المتاجر المحدّثة.
    ``fetch_fn`` قابلة للحقن (اختبار بلا شبكة).
    """
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    ts = datetime.now(timezone.utc).isoformat()
    updated = 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
                "store_name TEXT PRIMARY KEY, rating_avg REAL, rating_count INTEGER, "
                "trust_score REAL, updated_at TEXT)"
            )
            for comp in competitors:
                sid = int(getattr(comp, "mahally_store_id", 0) or 0)
                name = str(getattr(comp, "name", "") or "").strip()
                if not sid or not name:
                    continue
                info = fetch_fn(sid)
                if not info:
                    continue
                with conn:
                    conn.execute(
                        f"INSERT INTO {_TABLE} "
                        "(store_name, rating_avg, rating_count, trust_score, updated_at) "
                        "VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(store_name) DO UPDATE SET "
                        "rating_avg=excluded.rating_avg, rating_count=excluded.rating_count, "
                        "trust_score=excluded.trust_score, updated_at=excluded.updated_at",
                        (
                            name, float(info.get("rating_avg", 0) or 0),
                            int(info.get("rating_count", 0) or 0),
                            float(info.get("trust_score", 0) or 0), ts,
                        ),
                    )
                updated += 1
            return updated
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("refresh_store_profiles فشل: %s", exc)
        return updated


def store_profile_map(db_path: Optional[str] = None) -> dict[str, dict]:
    """قراءة ``{store_name: {rating_avg, rating_count, trust_score}}``. ``{}`` آمن."""
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                f"SELECT store_name, rating_avg, rating_count, trust_score FROM {_TABLE}"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {
        str(r[0]): {
            "rating_avg": float(r[1] or 0),
            "rating_count": int(r[2] or 0),
            "trust_score": float(r[3] or 0),
        }
        for r in rows
    }


def store_rating_badge(info: Optional[dict]) -> str:
    """نص وسم تقييم المتجر (عرض فقط). فارغ آمن عند غياب البيانات/التقييم."""
    if not info:
        return ""
    count = int(info.get("rating_count", 0) or 0)
    avg = float(info.get("rating_avg", 0) or 0)
    if count <= 0 or avg <= 0:
        return ""
    return f"🏪 تقييم المتجر ★{avg:.1f} ({count:,} تقييم)"


# عتبة «المتجر الصاعد»: أضاف ≥ هذا العدد من المنتجات الجديدة خلال النافذة.
# تحقّق حيّ 2026-07-10: العتبة 50/7أيام تفصل 9 متاجر صاعدة عن البقية بوضوح.
RISING_MIN_NEW = 50
RISING_WINDOW_DAYS = 7


def rising_store_names(
    days: int = RISING_WINDOW_DAYS, min_new: int = RISING_MIN_NEW,
    db_path: Optional[str] = None,
) -> set:
    """أسماء المتاجر «الصاعدة»: أضافت ≥``min_new`` منتجاً خلال ``days`` (من scrape_runs).

    آمنة تماماً: أي خطأ/غياب جدول ⇒ ``set()`` فارغة (لا رمز صعود).
    """
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT competitor, SUM(new_count) FROM scrape_runs "
                "WHERE run_at >= datetime('now', ?) GROUP BY competitor "
                "HAVING SUM(new_count) >= ?",
                (f"-{int(days)} days", int(min_new)),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return set()
    return {str(r[0]) for r in rows if r[0]}


# ── كاش عملية + وسم بالاسم (لإعادة الاستخدام عبر كل صفحات البطاقات) ──────
_PROC_CACHE: dict = {}
_PROC_LOADED = False
_RISING_CACHE: set = set()
_RISING_LOADED = False


def cached_profiles(force: bool = False) -> dict:
    """خريطة تقييم المتاجر مُخبّأة على مستوى العملية. ``{}`` آمن عند أي خطأ."""
    global _PROC_LOADED
    if _PROC_LOADED and not force:
        return _PROC_CACHE
    try:
        _PROC_CACHE.clear()
        _PROC_CACHE.update(store_profile_map())
        _PROC_LOADED = True
    except Exception:
        return {}
    return _PROC_CACHE


def cached_rising(force: bool = False) -> set:
    """أسماء المتاجر الصاعدة مُخبّأة على مستوى العملية. ``set()`` آمن."""
    global _RISING_LOADED, _RISING_CACHE
    if _RISING_LOADED and not force:
        return _RISING_CACHE
    try:
        _RISING_CACHE = rising_store_names()
        _RISING_LOADED = True
    except Exception:
        return set()
    return _RISING_CACHE


def store_badge_for_name(store_name: str) -> str:
    """وسم المتجر بالاسم: رمز «📈 صاعد» (إن نما) + تقييم المتجر (إن قُيّم). فارغ آمن.

    اسم المتجر على البطاقة (``المنافس``) يطابق مفتاح ``competitor_stores`` بدقّة
    (تحقّق حيّ 2026-07-10: 30/30). اسم غير مطابق (مثل «N متجر») ⇒ فارغ.
    """
    name = str(store_name or "").strip()
    if not name:
        return ""
    parts: list[str] = []
    try:
        if name in cached_rising():
            parts.append("📈 صاعد")
    except Exception:
        pass
    rate = store_rating_badge(cached_profiles().get(name))
    if rate:
        parts.append(rate)
    return " · ".join(parts)
