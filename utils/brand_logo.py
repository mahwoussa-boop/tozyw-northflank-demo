"""utils/brand_logo.py — جلب شعار الماركة عبر Google Custom Search (Image) API.

سلة تفرض شعاراً إلزامياً لإنشاء ماركة جديدة. هذه الوحدة تسحب رابط شعار للماركة
بالاسم من بحث صور Google (Programmable Search / Custom Search JSON API)، فيُمرَّر
في حمولة Make ضمن «صورة شعار الماركة» ليستخدمه Make عند إنشاء ماركة جديدة.

التهيئة عبر متغيّرات البيئة (.env):
  GOOGLE_CSE_KEY (أو GOOGLE_API_KEY)  ← مفتاح Google API.
  GOOGLE_CSE_ID  (أو GOOGLE_CSE_CX)   ← معرّف محرك البحث المبرمَج (cx).

آمن بالكامل: بلا مفاتيح أو عند أي فشل ⇒ "" (يُحذف الحقل من الـpayload فلا يُكسَر
الإرسال). **كاش دائم** (CSV) + كاش ذاكرة: مكالمة Google واحدة لكل ماركة (يبقى ضمن
الحصّة المجانية)؛ والنتيجة الفارغة تُخزَّن سلبياً (لا يُعاد الاستعلام لماركة بلا شعار).
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("BrandLogo")

_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_TIMEOUT = 12  # ثانية
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")

# كاش ذاكرة لكل عملية (المفتاح = الاسم المطبَّع). يحمل "" للمحاولات الفاشلة المُخزَّنة.
_MEM: dict[str, str] = {}


# ── التهيئة ─────────────────────────────────────────────────────────────────
def _api_key() -> str:
    return (os.environ.get("GOOGLE_CSE_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")).strip()


def _cse_id() -> str:
    return (os.environ.get("GOOGLE_CSE_ID", "")
            or os.environ.get("GOOGLE_CSE_CX", "")).strip()


def is_configured() -> bool:
    """True إذا توفّر مفتاح Google + معرّف محرك البحث — وإلا لا يُجرى استعلام."""
    return bool(_api_key() and _cse_id())


def _norm(name: str) -> str:
    return " ".join(str(name or "").split()).strip().lower()


# ── الكاش الدائم (CSV: brand_key,logo_url) ──────────────────────────────────
def _cache_path():
    from pathlib import Path
    override = os.environ.get("BRAND_LOGO_CACHE_PATH", "").strip()
    if override:
        return Path(override)
    from conf.constants import DATA_DIR
    return Path(DATA_DIR) / "brand_logo_cache.csv"


def _load_cache() -> dict[str, str]:
    """يقرأ كاش الشعارات من القرص (مفتاح مطبَّع → رابط؛ قد يكون فارغاً = محاولة فاشلة)."""
    path = _cache_path()
    out: dict[str, str] = {}
    try:
        if not path.exists():
            return out
        import csv
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                key = (row[0] or "").strip().lower()
                url = (row[1].strip() if len(row) > 1 else "")
                if key:
                    out[key] = url
    except Exception as e:
        logger.debug("تعذّر قراءة كاش الشعار: %s", e)
    return out


def _save_cache(key: str, url: str) -> None:
    """يُلحِق سطراً للكاش (إلحاق ذرّي بسيط؛ المفاتيح المكرّرة يفوز آخرها عند القراءة)."""
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import csv
        new = not path.exists()
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["brand_key", "logo_url"])
            w.writerow([key, url])
    except Exception as e:
        logger.debug("تعذّر حفظ كاش الشعار «%s»: %s", key[:40], e)


# ── استخراج رابط الشعار من رد Google CSE (خالص، قابل للاختبار) ───────────────
def _is_image_url(u: str) -> bool:
    s = str(u or "").strip().lower()
    if not s.startswith(("http://", "https://")):
        return False
    head = s.split("?", 1)[0]
    return any(head.endswith(ext) or ext in head for ext in _IMG_EXTS)


def pick_logo_url(cse_json: dict) -> str:
    """يستخرج أول رابط صورة صالح من رد Google CSE (image search). خالص.

    يفضّل ``items[].link`` (الصورة نفسها)، ويتدرّج إلى ``image.thumbnailLink``.
    """
    items = (cse_json or {}).get("items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        link = str(it.get("link") or "").strip()
        if _is_image_url(link):
            return link
    # تدرّج: مصغّرة من بيانات الصورة (روابط CSE قد لا تحمل امتداداً صريحاً)
    for it in items:
        if isinstance(it, dict):
            thumb = str((it.get("image") or {}).get("thumbnailLink") or "").strip()
            if thumb.startswith(("http://", "https://")):
                return thumb
    return ""


# ── استعلام Google (best-effort، قابل للحقن) ────────────────────────────────
def _search_google_logo(name: str, _get: Optional[Callable] = None) -> Optional[str]:
    """يستعلم بحث صور Google عن شعار الماركة. آمن وقابل للحقن.

    يعيد: رابطاً عند الإيجاد · "" إن هُيّئ ولم يجد (كاش سلبي) · ``None`` إن لم
    يُهيّأ أو حدث خطأ شبكي عابر (لا يُسمَّم الكاش فيُعاد المحاولة لاحقاً).
    """
    if not is_configured():
        return None
    getter = _get or _requests_get
    try:
        resp = getter(
            _ENDPOINT,
            params={
                "key": _api_key(), "cx": _cse_id(),
                "q": f"{name} logo", "searchType": "image",
                "num": 5, "safe": "active",
            },
            timeout=_TIMEOUT,
        )
        if getattr(resp, "status_code", 0) == 200:
            return pick_logo_url(resp.json() or {})
        logger.warning("بحث شعار «%s» رجع HTTP %s",
                       name[:40], getattr(resp, "status_code", "?"))
        return ""  # هُيّئ لكن لم ينجح (سلبي) — لا تُكرّر الاستعلام
    except Exception as e:
        logger.debug("بحث شعار «%s» تعذّر (عابر): %s", name[:40], e)
        return None  # خطأ عابر — لا تُخزّن سلبياً


def _requests_get(url, **kw):
    import requests
    return requests.get(url, **kw)


# ── الواجهة العامة ──────────────────────────────────────────────────────────
def get_brand_logo(brand_name: str, _fetch: Optional[Callable] = None) -> str:
    """يعيد رابط شعار للماركة بالاسم (كاش ذاكرة ← كاش قرص ← Google). "" عند التعذّر.

    ``_fetch`` قابل للحقن (اختبار): دالة (name)→Optional[str] بدل استعلام Google.
    """
    name = " ".join(str(brand_name or "").split()).strip()
    if not name:
        return ""
    key = _norm(name)
    if key in _MEM:
        return _MEM[key]
    # مسار سريع: بلا تهيئة Google (ولا حقن اختبار) لا مصدر للشعار — نتفادى قراءة
    # القرص لكل منتج في الدفعات الكبيرة، ونُخزّن "" مؤقتاً لهذه العملية.
    if _fetch is None and not is_configured():
        _MEM[key] = ""
        return ""
    cache = _load_cache()
    if key in cache:
        _MEM[key] = cache[key]
        return cache[key]
    fetch = _fetch or _search_google_logo
    result = fetch(name)
    if result is None:        # غير مُهيّأ أو خطأ عابر — لا تُخزّن، أعد "" مؤقتاً
        return ""
    _MEM[key] = result
    _save_cache(key, result)  # قد يكون "" (سلبي) — يمنع تكرار الاستعلام
    return result
