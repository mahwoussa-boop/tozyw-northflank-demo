"""services/store_discovery_service.py — اكتشاف متاجر عطور جديدة لم نكشطها بعد.

**معزول عن كاشط المنتجات.** يعدّ المتاجر البائعة للعطور في فهرس Algolia (facet على
``store_id`` باستعلام عطري)، يستبعد المتاجر المعروفة (معرّفاتنا)، ويجلب أسماء
وتقييمات أكبر المرشّحين المجهولين ليقرّر المالك إضافتهم. يخزّن النتيجة في جدول
``discovered_stores`` (كاش مشتق: DELETE+INSERT). أي فشل شبكة/قاعدة ⇒ لا اكتشاف
(تدهور لطيف — القسم يبقى فارغاً).

«بنفس المستوى»: عتبة ``min_products`` تُسقط المتاجر الصغيرة، فتبقى المتاجر الجادّة
(تحقّق حيّ 2026-07-10: 971 متجر عطور مجهول، أكبرها 29,052 منتجاً).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

_TABLE = "discovered_stores"
DISCOVER_QUERY = "عطر"
DISCOVER_TOP_N = 30
DISCOVER_MIN_PRODUCTS = 500


def facet_store_counts(query: str = DISCOVER_QUERY) -> dict[int, int]:
    """``{store_id: product_count}`` للمتاجر البائعة للعطور (facet). ``{}`` آمن."""
    try:
        import requests
        from engines.mahally_scraper import ALGOLIA_API_URL, ALGOLIA_HEADERS
        resp = requests.post(
            ALGOLIA_API_URL, headers=ALGOLIA_HEADERS,
            json={
                "query": query, "hitsPerPage": 0,
                "facets": ["store_id"], "maxValuesPerFacet": 1000,
            },
            timeout=25,
        )
        if resp.status_code != 200:
            return {}
        raw = resp.json().get("facets", {}).get("store_id", {})
        return {int(k): int(v) for k, v in raw.items()}
    except Exception as exc:  # pragma: no cover - مسار الشبكة
        logger.debug("facet_store_counts فشل: %s", exc)
        return {}


def fetch_store_meta(store_id: int) -> Optional[dict]:
    """اسم المتجر + تقييمه من hit واحد (خفيف). ``None`` آمن."""
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
                "attributesToRetrieve": ["store_name", "store_rating", "store_trust_score"],
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
            "store_name": str(hits[0].get("store_name", "") or ""),
            "rating_avg": float(sr.get("average") or 0),
            "rating_count": int(sr.get("count") or 0),
        }
    except Exception as exc:  # pragma: no cover - مسار الشبكة
        logger.debug("fetch_store_meta(%s) فشل: %s", store_id, exc)
        return None


def discover_new_stores(
    known_ids: Iterable[int],
    *,
    query: str = DISCOVER_QUERY,
    top_n: int = DISCOVER_TOP_N,
    min_products: int = DISCOVER_MIN_PRODUCTS,
    counts_fn: Callable[[str], dict[int, int]] = facet_store_counts,
    meta_fn: Callable[[int], Optional[dict]] = fetch_store_meta,
) -> list[dict]:
    """يُرجع أكبر المتاجر العطرية المجهولة: ``[{store_id, store_name, product_count, ...}]``.

    ``counts_fn`` و``meta_fn`` قابلتان للحقن (اختبار بلا شبكة).
    """
    known = {int(k) for k in known_ids if k}
    counts = counts_fn(query) or {}
    unknown = sorted(
        ((sid, c) for sid, c in counts.items() if sid not in known and c >= min_products),
        key=lambda x: -x[1],
    )[:top_n]
    out: list[dict] = []
    for sid, cnt in unknown:
        meta = meta_fn(sid) or {}
        out.append({
            "store_id": sid,
            "store_name": str(meta.get("store_name", "") or ""),
            "product_count": int(cnt),
            "rating_avg": float(meta.get("rating_avg", 0) or 0),
            "rating_count": int(meta.get("rating_count", 0) or 0),
        })
    return out


def refresh_discovered_stores(
    known_ids: Iterable[int], *, db_path: Optional[str] = None, **kwargs,
) -> int:
    """يكتشف ويخزّن في ``discovered_stores`` (DELETE+INSERT ذرّي). يُرجع العدد؛ خطأ⇒0."""
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    try:
        found = discover_new_stores(known_ids, **kwargs)
        ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
                "store_id INTEGER PRIMARY KEY, store_name TEXT, product_count INTEGER, "
                "rating_avg REAL, rating_count INTEGER, discovered_at TEXT)"
            )
            rows = [
                (int(d["store_id"]), str(d["store_name"]), int(d["product_count"]),
                 float(d["rating_avg"]), int(d["rating_count"]), ts)
                for d in found
            ]
            with conn:
                conn.execute(f"DELETE FROM {_TABLE}")
                conn.executemany(
                    f"INSERT INTO {_TABLE} "
                    "(store_id, store_name, product_count, rating_avg, rating_count, discovered_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
            return len(rows)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("refresh_discovered_stores فشل: %s", exc)
        return 0


def discovered_stores_list(db_path: Optional[str] = None) -> list[dict]:
    """قراءة المتاجر المكتشفة مرتبة بعدد المنتجات تنازلياً. ``[]`` آمن."""
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                f"SELECT store_id, store_name, product_count, rating_avg, rating_count "
                f"FROM {_TABLE} ORDER BY product_count DESC"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [
        {
            "store_id": int(r[0]), "store_name": str(r[1] or ""),
            "product_count": int(r[2] or 0), "rating_avg": float(r[3] or 0),
            "rating_count": int(r[4] or 0),
        }
        for r in rows
    ]
