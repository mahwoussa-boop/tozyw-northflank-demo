"""
utils/salla_api.py — عميل Salla Admin API صغير (إنشاء الماركة/التصنيف عند الغياب)
═══════════════════════════════════════════════════════════════════════════════
يُستخدم في مسار create-if-missing داخل make_helper:
  عند غياب brand_id/category_id ⇒ ابحث بالاسم، وإن لم يوجد أنشئه عبر API،
  ثم استخدم الـid الناتج (وحدّث الخريطة المحلية في missing_queue_manager).

التهيئة عبر متغيّرات البيئة (.env):
  SALLA_ACCESS_TOKEN  ← توكن وصول Salla (OAuth Bearer). إلزامي لأي استدعاء.
  SALLA_API_BASE      ← اختياري، الافتراضي https://api.salla.dev/admin/v2

آمن بالكامل: إن لم يُهيّأ التوكن أو فشل أي استدعاء ⇒ تُعيد الدوال None بلا رفع
استثناء، فيعود السلوك إلى الافتراضي (يُحذف الحقل من الـpayload) دون كسر الإرسال.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger("SallaAPI")

_TIMEOUT = 15  # ثانية
_DEFAULT_BASE = "https://api.salla.dev/admin/v2"


# ── التهيئة ─────────────────────────────────────────────────────────────────
def _base() -> str:
    return (os.environ.get("SALLA_API_BASE", "") or _DEFAULT_BASE).rstrip("/")


def _token() -> str:
    return (
        os.environ.get("SALLA_ACCESS_TOKEN", "")
        or os.environ.get("SALLA_API_TOKEN", "")
    ).strip()


def is_configured() -> bool:
    """True إذا توفّر توكن Salla — وإلا لا يُجرى أي استدعاء API."""
    return bool(_token())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _norm(s) -> str:
    return " ".join(str(s or "").split()).strip().lower()


# ── البحث (best-effort، غير قاتل) ──────────────────────────────────────────
def _search(endpoint: str, name: str) -> Optional[int]:
    """يبحث عن عنصر بالاسم (تطابق مطبَّع). يتفادى إنشاء مكرّر في المتجر الحيّ."""
    try:
        r = requests.get(
            f"{_base()}/{endpoint}", headers=_headers(),
            params={"keyword": name}, timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or []
            target = _norm(name)
            for item in data:
                if isinstance(item, dict) and _norm(item.get("name")) == target and item.get("id"):
                    return int(item["id"])
    except Exception as e:
        logger.debug("بحث %s عن «%s» تعذّر: %s", endpoint, str(name)[:40], e)
    return None


# ── الإنشاء ─────────────────────────────────────────────────────────────────
def _create(endpoint: str, name: str) -> Optional[int]:
    try:
        r = requests.post(
            f"{_base()}/{endpoint}", headers=_headers(),
            json={"name": name}, timeout=_TIMEOUT,
        )
        if r.status_code in (200, 201):
            data = (r.json() or {}).get("data") or {}
            _id = data.get("id") if isinstance(data, dict) else None
            if _id:
                return int(_id)
        logger.warning(
            "إنشاء %s «%s» رجع HTTP %s: %s",
            endpoint, str(name)[:40], r.status_code, r.text[:200],
        )
    except Exception as e:
        logger.warning("إنشاء %s «%s» فشل: %s", endpoint, str(name)[:40], e)
    return None


# ── الواجهة العامة: ابحث ثم أنشئ ────────────────────────────────────────────
def get_or_create_brand(name: str) -> Optional[int]:
    """يعيد brand_id لماركة بالاسم (يبحث أولاً ثم يُنشئ). None إن تعذّر/بلا توكن."""
    if not is_configured() or not str(name).strip():
        return None
    return _search("brands", name) or _create("brands", name)


def get_or_create_category(name: str) -> Optional[int]:
    """يعيد category_id لتصنيف بالاسم (يبحث أولاً ثم يُنشئ). None إن تعذّر/بلا توكن."""
    if not is_configured() or not str(name).strip():
        return None
    return _search("categories", name) or _create("categories", name)


def update_product(product_id, _put=None, **fields) -> Optional[dict]:
    """يعدّل منتجاً موجوداً في سلة تعديلاً **موجّهاً** (PUT يُرسل الحقول المُمرَّرة فقط).

    للاستخدام: تحديث ماركة/تصنيف/SEO لمنتج قائم (brand_id, category_id,
    metadata_title, metadata_description, …). **لا يُستخدَم لمسار التسعير** —
    الحقول الفارغة تُسقَط هنا صراحةً كي لا يمحو تحديثٌ موجّهٌ بياناتٍ قائمة.

    آمن: بلا توكن، أو معرّف فارغ، أو لا حقول صالحة، أو عند الفشل ⇒ ``None``.
    ``_put`` قابل للحقن (اختبار). يعيد ``data`` من رد سلة عند النجاح.
    """
    pid = str(product_id or "").strip()
    # أسقط الحقول الفارغة (PATCH موجّه — لا نرسل ما يمحو البيانات)
    payload = {k: v for k, v in fields.items() if v not in (None, "", [], {})}
    if not is_configured() or not pid or not payload:
        return None
    put = _put or requests.put
    try:
        r = put(
            f"{_base()}/products/{pid}", headers=_headers(),
            json=payload, timeout=_TIMEOUT,
        )
        if getattr(r, "status_code", 0) in (200, 201):
            return (r.json() or {}).get("data") or {}
        logger.warning(
            "تعديل المنتج %s رجع HTTP %s: %s",
            pid, getattr(r, "status_code", "?"), getattr(r, "text", "")[:200],
        )
    except Exception as e:
        logger.warning("تعديل المنتج %s فشل: %s", pid, e)
    return None


# ════════════════════════════════════════════════════════════════════════════
#  سحب كتالوج المتجر (Products) — تحديث آلي بدل التصدير اليدوي
# ════════════════════════════════════════════════════════════════════════════
#  يزيل خطر «تصدير قديم ⇒ تسرّب تكرار»: يسحب منتجات المتجر الحيّة بترقيم
#  صفحات، ويُطبّعها لنفس أعمدة بصمة الكتالوج (أسم المنتج/الماركة/sku/الباركود)
#  فتُمرَّر مباشرةً إلى ``screen_for_duplicates``/``enqueue_missing_products``
#  أو تُسنَد لـ ``state.our_catalog``.

def _norm_product(item) -> Optional[dict]:
    """يطبّع منتج Salla إلى صفّ كتالوج (نفس مفاتيح بصمة الكتالوج). خالص."""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    brand = item.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or ""
    elif not isinstance(brand, str):
        brand = ""
    barcode = item.get("barcode") or item.get("gtin") or item.get("mpn") or ""
    return {
        "أسم المنتج":     name,
        "الماركة":        str(brand or "").strip(),
        "رمز المنتج sku": str(item.get("sku") or "").strip(),
        "الباركود":       str(barcode or "").strip(),
        "id":             item.get("id"),
    }


def _paginate(endpoint: str, per_page: int = 65, max_pages: int = 200, _get=None) -> list:
    """يكرّر صفحات ``endpoint`` ويُرجع **كل** عناصر ``data`` الخام عبر الترقيم.

    آمن: بلا توكن أو أي خطأ ⇒ ``[]``. ``_get`` قابل للحقن (اختبار). يتوقّف عند
    آخر صفحة (``currentPage>=totalPages``) أو فراغ ``data`` أو ``max_pages``.
    """
    if not is_configured():
        return []
    get = _get or requests.get
    out: list = []
    page = 1
    while page <= max_pages:
        try:
            r = get(
                f"{_base()}/{endpoint}", headers=_headers(),
                params={"page": page, "per_page": per_page}, timeout=_TIMEOUT,
            )
        except Exception as e:
            logger.warning("سحب %s صفحة %d تعذّر: %s", endpoint, page, e)
            break
        if getattr(r, "status_code", 0) != 200:
            logger.warning("سحب %s رجع HTTP %s", endpoint, getattr(r, "status_code", "?"))
            break
        try:
            body = r.json() or {}
        except Exception:
            break
        data = body.get("data") or []
        if not data:
            break
        out.extend(data)
        pag = body.get("pagination") or {}
        cur = int(pag.get("currentPage") or page)
        total = int(pag.get("totalPages") or cur)
        if cur >= total:
            break
        page += 1
    return out


def fetch_store_products(
    per_page: int = 65,
    max_pages: int = 200,
    _get=None,
) -> list:
    """يسحب كل منتجات المتجر (مطبَّعة لأعمدة الكتالوج) عبر ترقيم الصفحات.

    آمن: بلا توكن أو عند أي خطأ ⇒ ``[]``. ``_get`` قابل للحقن للاختبار.
    """
    out: list = []
    for it in _paginate("products", per_page=per_page, max_pages=max_pages, _get=_get):
        row = _norm_product(it)
        if row:
            out.append(row)
    logger.info("📦 سُحِب %d منتج من كتالوج المتجر (Salla)", len(out))
    return out


def store_catalog_dataframe(per_page: int = 65, max_pages: int = 200, _get=None):
    """يسحب كتالوج المتجر كـ DataFrame (أعمدة البصمة). None إن لم يُهيّأ/فارغ.

    يُسنَد لـ ``state.our_catalog`` أو يُمرَّر لبوابة منع التكرار بدل التصدير اليدوي.
    """
    rows = fetch_store_products(per_page=per_page, max_pages=max_pages, _get=_get)
    if not rows:
        return None
    import pandas as pd
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════════
#  مزامنة الماركات/التصنيفات بمعرّفاتها من سلة (لحلّ brand_id/category_id)
# ════════════════════════════════════════════════════════════════════════════
#  المشكلة: load_brand_id_map/load_category_catalog يقرآن data/*.csv الفارغين،
#  فتُحلّ الـIDs إلى None وتُحذف من الحمولة. الحل: سحب الماركات/التصنيفات بمعرّفاتها
#  الحيّة وكتابتها في الخرائط المحلية، فيُحلّ ID لكل منتج باسم ماركة/تصنيف موجود.

def fetch_brands(_get=None) -> list:
    """يسحب ماركات المتجر بمعرّفاتها: ``[{name, id}]``. آمن: بلا توكن ⇒ ``[]``."""
    out = []
    for it in _paginate("brands", _get=_get):
        if isinstance(it, dict) and str(it.get("name") or "").strip() and it.get("id"):
            out.append({"name": str(it["name"]).strip(), "id": it["id"]})
    return out


def fetch_categories(_get=None) -> list:
    """يسحب تصنيفات المتجر بمعرّفاتها: ``[{name, id}]``. آمن: بلا توكن ⇒ ``[]``."""
    out = []
    for it in _paginate("categories", _get=_get):
        if isinstance(it, dict) and str(it.get("name") or "").strip() and it.get("id"):
            out.append({"name": str(it["name"]).strip(), "id": it["id"]})
    return out


def sync_store_maps(_get=None) -> dict:
    """يزامن خرائط الماركات/التصنيفات بمعرّفات سلة الحيّة إلى ``data/*.csv``.

    يكتب ``brand_catalog.csv`` (brand_key,brand_name,brand_id) و
    ``category_catalog.csv`` (category_name,category_id) عبر ``upsert_brand_id``/
    ``upsert_category_id`` الموجودتين ⇒ بعدها يُحلّ ``brand_id``/``category_id``
    لكل منتج باسم ماركة/تصنيف موجود في المتجر. يتطلّب توكن.

    يعيد ``{configured, brands, categories}``. آمن: بلا توكن ⇒ أصفار.
    """
    if not is_configured():
        return {"configured": False, "brands": 0, "categories": 0}
    brands = fetch_brands(_get=_get)
    cats = fetch_categories(_get=_get)
    nb = nc = 0
    try:
        from utils.missing_queue_manager import upsert_brand_id, upsert_category_id
        for b in brands:
            upsert_brand_id(b["name"], b["id"])
            nb += 1
        for c in cats:
            upsert_category_id(c["name"], c["id"])
            nc += 1
    except Exception as e:
        logger.warning("مزامنة خرائط الماركات/التصنيفات تعذّرت: %s", e)
    # مرآة «مصدر الحقيقة» إلى Supabase (اختياري؛ القراءة الحارّة تبقى على CSV).
    sb = 0
    try:
        from utils.supabase_client import upsert_store_maps
        sb = upsert_store_maps(brands, cats)
    except Exception as e:
        logger.warning("upsert Supabase تعذّر: %s", e)
    logger.info("🔄 مزامنة: %d ماركة + %d تصنيف بمعرّفاتها (Supabase: %d صف)", nb, nc, sb)
    return {"configured": True, "brands": nb, "categories": nc, "supabase": sb}


# كاش ذاكرة لكتالوج المتجر (يتفادى إعادة سحب كل المتجر في كل إرسال).
_CATALOG_CACHE: dict = {"df": None, "ts": 0.0}


def get_store_catalog_df(force: bool = False, ttl: float = 3600.0):
    """كتالوج المتجر من API مع كاش ذاكرة (TTL ساعة). None إن لم يُهيّأ/فارغ/خطأ.

    تستخدمه بوابة منع التكرار تلقائياً حين يغيب التصدير اليدوي (وتوكن Salla
    مُهيّأ) ⇒ كتالوج حيّ بلا «تصدير قديم». ``force`` يتجاهل الكاش.
    """
    import time
    if not is_configured():
        return None
    now = time.time()
    cached = _CATALOG_CACHE.get("df")
    if not force and cached is not None and (now - _CATALOG_CACHE.get("ts", 0.0)) < ttl:
        return cached
    df = store_catalog_dataframe()
    if df is not None:
        _CATALOG_CACHE["df"] = df
        _CATALOG_CACHE["ts"] = now
    return df
