"""
utils/make_helper.py v25.0 — إرسال صحيح لـ Make.com + عمود NO
══════════════════════════════════════════════════════════════
v25.0 additions:
  + حقل NO (رقم المنتج من كتالوج سلة/زد — Primary Key "No.") يُمرَّر صراحةً
    في كل payload. Make.com يستخدمه لتحديث المنتج الصحيح بدقة مطلقة.
  + دالة _extract_no() تقرأ "No." / "NO" / "رقم المنتج" من dict أو Series.
  + في حال غياب product_id الصريح، نستخدم NO كبديل أساسي.

سيناريو تحديث الأسعار (Integration Webhooks, Salla):
  Webhook → BasicFeeder يقرأ {{2.products}} → UpdateProduct
  Payload المطلوب: {"products": [{"NO":"...","product_id":"...","name":"...","price":...}]}

سيناريو المنتجات الجديدة:
  Webhook → BasicFeeder يقرأ {{1.data}} → CreateProduct
  Payload المطلوب: {"data": [{"NO":"...","أسم المنتج":"...","سعر المنتج":...,"الوصف":"..."}]}
"""

import requests
import json
import logging
import os
import re
import time
from typing import List, Dict, Any, Optional

logger = logging.getLogger("MakeHelper")


# ── Webhook URLs ───────────────────────────────────────────────────────────
def _get_webhook_url(key: str, default: str) -> str:
    return os.environ.get(key, "") or default

WEBHOOK_UPDATE_PRICES = _get_webhook_url(
    "WEBHOOK_UPDATE_PRICES",
    "https://hook.eu2.make.com/YOUR_WEBHOOK_URL_HERE"
)
WEBHOOK_NEW_PRODUCTS = _get_webhook_url(
    "WEBHOOK_NEW_PRODUCTS",
    "https://hook.eu2.make.com/YOUR_WEBHOOK_URL_HERE"
)

TIMEOUT = 15  # ثانية


# ── الإرسال الأساسي ────────────────────────────────────────────────────────
def parse_webhook_result(http_ok: bool, status_code: int, body: str) -> Dict:
    """يحلّل ردّ الويبهوك **بصدق** (دالة خالصة). متوافق مع إصلاح Make القادم الذي
    سيعيد JSON فيه ``ok``/``product_id`` عند نهاية السيناريو، ومع الحالة الراهنة
    (ردّ فوري ``Accepted`` قبل اكتمال السيناريو — لا يؤكّد الإنشاء في سلة).

    Returns ``{"state": confirmed|accepted|failed, "product_id": ..|None, "message": str}``:
      • ليس 2xx                          ⇒ failed (رفض/شبكة) بنصّ الرد.
      • 2xx + JSON ``{ok:true, product_id}`` ⇒ confirmed «✅ أُنشئ في سلة (رقم N)».
      • 2xx + JSON ``{ok:false, error}``     ⇒ failed «❌ رفضته سلة: …».
      • 2xx بلا JSON اكتمال (Accepted/فارغ)  ⇒ accepted «📨 أُرسل — تحقّق من سلة».
    """
    if not http_ok:
        _b = (body or "").strip()[:160]
        return {"state": "failed", "product_id": None,
                "message": f"❌ فشل الإنشاء (HTTP {status_code})" + (f": {_b}" if _b else "")}
    data = None
    _b = (body or "").strip()
    if _b[:1] in ("{", "["):
        try:
            import json as _json
            data = _json.loads(_b)
            if isinstance(data, list):
                data = data[0] if data else None
        except Exception:
            data = None
    if isinstance(data, dict) and ("ok" in data or "success" in data):
        if bool(data.get("ok", data.get("success"))):
            pid = data.get("product_id") or data.get("id") or ""
            return {"state": "confirmed", "product_id": (pid or None),
                    "message": f"✅ أُنشئ في سلة (رقم {pid})" if pid else "✅ أُنشئ في سلة"}
        err = str(data.get("error") or data.get("message") or "").strip()[:160]
        return {"state": "failed", "product_id": None,
                "message": f"❌ رفضته سلة: {err}" if err else "❌ رفضته سلة"}
    return {"state": "accepted", "product_id": None,
            "message": "📨 أُرسل إلى Make — تحقّق من ظهوره في سلة"}


def _post_to_webhook(url: str, payload: Any) -> Dict:
    if not url:
        return {"success": False, "message": "❌ Webhook URL غير محدد",
                "status_code": 0, "body": "", "state": "failed", "product_id": None}
    # ── السياج الظِّلّي (م2): تدوين فقط — لا يغيّر السعر ولا يمنع الإرسال ──
    blocked_low_price: list = []
    try:
        from services.pricing_shadow import record_shadow
        if isinstance(payload, dict):
            record_shadow(payload.get("products") or payload.get("data") or [],
                          path="helper")
    except Exception:
        pass
    # ── أرضية خطأ الكشط (أ2): يحجز سعراً < 50% من وسيط السوق للمراجعة ──
    if isinstance(payload, dict):
        from services.send_floor_guard import split_below_floor
        env_key = "products" if "products" in payload else "data"
        if env_key in payload:
            payload[env_key], blocked_low_price = split_below_floor(payload[env_key])
            if not payload[env_key]:
                return {"success": True, "message": "📭 لا عناصر بعد أرضية الأسعار",
                        "status_code": 0, "body": "", "state": "blocked_low_price",
                        "product_id": None, "blocked_low_price": blocked_low_price}
    # ── سياج نطاق v1 (أ4، مرحلة 1 — قسم "سعر أعلى" فقط): يحجز خارج ±20% من الوسيط ──
    held_outside_band: list = []
    if isinstance(payload, dict):
        from services.send_band_guard import split_outside_band
        env_key = "products" if "products" in payload else "data"
        if env_key in payload:
            payload[env_key], held_outside_band = split_outside_band(payload[env_key])
            if not payload[env_key]:
                return {"success": True, "message": "📭 لا عناصر بعد سياج نطاق v1",
                        "status_code": 0, "body": "", "state": "held_outside_band",
                        "product_id": None, "blocked_low_price": blocked_low_price,
                        "held_outside_band": held_outside_band}
            from services.send_band_guard import record_and_strip_band_overrides
            payload[env_key] = record_and_strip_band_overrides(payload[env_key])
    try:
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        body = (getattr(resp, "text", "") or "")[:1000]
        http_ok = resp.status_code in (200, 201, 202, 204)
        outcome = parse_webhook_result(http_ok, resp.status_code, body)
        result = {
            "success": http_ok and outcome["state"] != "failed",
            "message": outcome["message"],
            "status_code": resp.status_code,
            "body": body,
            "state": outcome["state"],
            "product_id": outcome["product_id"],
        }
        if blocked_low_price:
            result["blocked_low_price"] = blocked_low_price
        if held_outside_band:
            result["held_outside_band"] = held_outside_band
        return result
    except requests.exceptions.Timeout:
        return {"success": False, "message": "❌ انتهت مهلة الاتصال (Timeout)",
                "status_code": 0, "body": "", "state": "failed", "product_id": None}
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "❌ فشل الاتصال بـ Make — تحقق من الإنترنت",
                "status_code": 0, "body": "", "state": "failed", "product_id": None}
    except Exception as e:
        return {"success": False, "message": f"❌ خطأ غير متوقع: {str(e)}",
                "status_code": 0, "body": "", "state": "failed", "product_id": None}


# ── تحويل float آمن ───────────────────────────────────────────────────────
def _safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None or str(val).strip() in ("", "nan", "None", "NaN"):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


# ── تنظيف product_id ──────────────────────────────────────────────────────
def _clean_pid(raw) -> str:
    """product_id دائماً كـ str(int(float(value))). مثال: '100.0' → '100'."""
    if raw is None: return ""
    s = str(raw).strip()
    if s in ("", "nan", "None", "NaN", "0", "0.0"): return ""
    try:
        return str(int(float(s)))
    except (ValueError, TypeError):
        return s


def _pid_as_int(raw) -> Optional[int]:
    """product_id كرقم صحيح لموديول Salla UpdateProduct (select field)."""
    s = _clean_pid(raw)
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


# ── استخراج رقم المنتج No. من الكتالوج (Primary Key في سلة/زد) ───────────
def _extract_no(row_or_dict) -> str:
    """
    يستخرج قيمة عمود "No." (رقم المنتج في كتالوجنا) من صف DataFrame أو dict.
    هذا هو المعرّف الرسمي الذي يستخدمه Make.com لتحديث المنتج في سلة/زد.
    يتحقق من عدة أسماء محتملة ويُنظّف القيمة نهائياً عبر _clean_pid().
    """
    if row_or_dict is None:
        return ""
    getter = row_or_dict.get if hasattr(row_or_dict, "get") else lambda k, d=None: d
    raw = (
        getter("No.")          or getter("NO")             or
        getter("no")           or getter("No")             or
        getter("رقم_المنتج")   or getter("رقم المنتج")    or
        getter("catalog_no")   or getter("product_no")     or ""
    )
    return _clean_pid(raw)


# ── ربط اسم التصنيف → category_id (جدول الربط في missing_queue_manager) ────
def _resolve_category_id(p: Dict) -> Optional[int]:
    """يحوّل اسم التصنيف → category_id رقمي عبر جدول الربط الموجود.
    يقبل category_id رقمياً جاهزاً أولاً؛ وإلا يبحث باسم التصنيف (عدة مفاتيح محتملة).
    يعيد None إن تعذّر (فيُحذف لاحقاً من الـ payload — لا يُرسَل صفر/فارغ)."""
    raw_id = _safe_float(p.get("category_id", 0))
    if raw_id:
        return int(raw_id)
    name = str(
        p.get("اسم التصنيف")    or p.get("category_name") or p.get("التصنيف") or
        p.get("تصنيف_المنتج")   or p.get("التصنيف_الرسمي") or p.get("category") or ""
    ).strip()
    if not name:
        return None
    try:
        from utils.missing_queue_manager import load_category_catalog
        cid = _safe_float(load_category_catalog().get(name, 0))
        if cid:
            return int(cid)
    except Exception as e:
        logger.warning("تعذّر ربط التصنيف «%s» بـ category_id: %s", name[:40], e)
    # create-if-missing عبر Salla API (يُحدّث الخريطة المحلية)
    return _create_category_via_salla(name)


# ── ربط اسم الماركة → brand_id (جدول الربط في missing_queue_manager) ───────
def _resolve_brand_id(p: Dict) -> Optional[int]:
    """يحوّل اسم الماركة → brand_id رقمي عبر load_brand_id_map.
    يقبل brand_id رقمياً جاهزاً أولاً؛ وإلا يطبّع اسم الماركة بنفس مفتاح
    missing_queue_manager (_brand_key) ثم يبحث. يعيد None إن تعذّر (يُحذف من الـ payload)."""
    raw_id = _safe_float(p.get("brand_id", 0))
    if raw_id:
        return int(raw_id)
    name = str(p.get("brand") or p.get("الماركة") or p.get("brand_name") or "").strip()
    if not name:
        return None
    try:
        from utils.missing_queue_manager import load_brand_id_map, _brand_key
        bid = _safe_float(load_brand_id_map().get(_brand_key(name), 0))
        if bid:
            return int(bid)
    except Exception as e:
        logger.warning("تعذّر ربط الماركة «%s» بـ brand_id: %s", name[:40], e)
    # create-if-missing عبر Salla API (يُحدّث الخريطة المحلية)
    return _create_brand_via_salla(name)


# ── create-if-missing عبر Salla API (كاش لكل عملية لتفادي تكرار الاستدعاء) ──
_BRAND_ID_CACHE: Dict[str, Optional[int]] = {}
_CATEGORY_ID_CACHE: Dict[str, Optional[int]] = {}


def _create_brand_via_salla(name: str) -> Optional[int]:
    """يبحث/يُنشئ الماركة في سلة عبر API ويعيد brand_id. None إن تعذّر/بلا توكن.
    يُحدّث brand_catalog.csv ويُخزّن في كاش لكل عملية."""
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key in _BRAND_ID_CACHE:
        return _BRAND_ID_CACHE[key]
    bid: Optional[int] = None
    try:
        from utils.salla_api import get_or_create_brand, is_configured
        if is_configured():
            bid = get_or_create_brand(name)
            if bid:
                from utils.missing_queue_manager import upsert_brand_id
                upsert_brand_id(name, bid)
                logger.info("🏷️ ماركة سلة «%s» → brand_id=%s", name[:40], bid)
    except Exception as e:
        logger.warning("تعذّر إنشاء الماركة «%s» عبر Salla API: %s", name[:40], e)
    _BRAND_ID_CACHE[key] = bid
    return bid


def _create_category_via_salla(name: str) -> Optional[int]:
    """يبحث/يُنشئ التصنيف في سلة عبر API ويعيد category_id. None إن تعذّر/بلا توكن.
    يُحدّث category_catalog.csv ويُخزّن في كاش لكل عملية."""
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key in _CATEGORY_ID_CACHE:
        return _CATEGORY_ID_CACHE[key]
    cid: Optional[int] = None
    try:
        from utils.salla_api import get_or_create_category, is_configured
        if is_configured():
            cid = get_or_create_category(name)
            if cid:
                from utils.missing_queue_manager import upsert_category_id
                upsert_category_id(name, cid)
                logger.info("🗂️ تصنيف سلة «%s» → category_id=%s", name[:40], cid)
    except Exception as e:
        logger.warning("تعذّر إنشاء التصنيف «%s» عبر Salla API: %s", name[:40], e)
    _CATEGORY_ID_CACHE[key] = cid
    return cid


# ── الاسم النصّي للماركة / التصنيف (يُرسَل دائماً ليُنشئه Make عند الغياب) ──
# قيم نائبة لا تُعدّ ماركة حقيقية: تُسقَط كي لا يُنشئ Make ماركةً وهمية («غير متوفر»).
_BRAND_BLANKS = frozenset({"", "nan", "none", "null", "غير متوفر", "غير محدد", "-"})


def _brand_name(p: Dict) -> str:
    """اسم الماركة للحمولة — يُرسَل دائماً نصّياً (Make يُنشئه عند الغياب).

    يُسقط القيم النائبة (غير متوفر/nan/…)، ويتدرّج لاستخراج الماركة من اسم المنتج
    كملاذ أخير كي لا تُرسَل الماركة فارغة (طلب المالك: الماركة مملوءة دائماً)."""
    raw = str(
        p.get("الماركة") or p.get("الماركة_الرسمية") or
        p.get("brand")   or p.get("brand_name") or ""
    ).strip()
    if raw.lower() not in _BRAND_BLANKS:
        return raw
    # تدرّج: استخراج الماركة من اسم المنتج عبر محرك الماركات (لا اختلاق — قائمة معروفة).
    name = str(
        p.get("أسم المنتج") or p.get("name") or p.get("المنتج") or
        p.get("منتج_المنافس") or ""
    ).strip()
    if name:
        try:
            from utils.salla_shamel_export import _brand_from_name
            extracted = str(_brand_from_name(name) or "").strip()
            if extracted and extracted.lower() not in _BRAND_BLANKS:
                return extracted
        except Exception:
            pass
    return ""


def _category_name(p: Dict) -> str:
    return str(
        p.get("اسم التصنيف")  or p.get("category_name") or
        p.get("تصنيف_المنتج") or p.get("التصنيف_الرسمي") or
        p.get("التصنيف")      or p.get("category") or ""
    ).strip()


# ── شعار الماركة (لإنشاء ماركة جديدة في سلة عبر Make) ──────────────────────
def _brand_logo(p: Dict, brand: str) -> str:
    """رابط شعار الماركة للحمولة: شعار صريح من الصف، وإلا بحث صور Google (مكاش).

    سلة تفرض شعاراً لإنشاء ماركة جديدة؛ يُمرَّر هنا ليستخدمه Make عند الإنشاء.
    آمن: بلا مفاتيح Google أو عند الفشل ⇒ "" (يُحذف الحقل، لا يُكسَر الإرسال)."""
    explicit = str(
        p.get("صورة شعار الماركة") or p.get("brand_logo") or
        p.get("شعار_الماركة") or p.get("logo_url") or ""
    ).strip()
    if explicit.lower().startswith(("http://", "https://")):
        return explicit
    if not brand:
        return ""
    try:
        from utils.brand_logo import get_brand_logo
        return get_brand_logo(brand)
    except Exception as e:
        logger.debug("تعذّر جلب شعار الماركة «%s»: %s", brand[:40], e)
        return ""


# ── جمع روابط الصور المتعددة (روابط مباشرة فقط، الرئيسية أولاً، بلا تكرار) ──
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _is_direct_image_url(u: str) -> bool:
    """رابط صورة مباشر (http/https + امتداد صورة) — يستبعد data: و placeholders."""
    s = str(u or "").strip()
    if not s.lower().startswith(("http://", "https://", "//")):
        return False
    low = s.lower().split("?", 1)[0]
    return any(ext in low for ext in _IMG_EXTS)


def _collect_image_urls(p: Dict) -> List[str]:
    """يجمع كل روابط صور المنتج: الصورة الرئيسية أولاً ثم البقية، بلا تكرار.
    يقبل المصدر كقائمة، أو قائمة dicts {"src":..}، أو نص JSON من قاعدة البيانات."""
    main = str(
        p.get("صورة المنتج") or p.get("image_url") or p.get("صورة_المنافس") or ""
    ).strip()

    raw = p.get("صور المنتج") or p.get("image_urls") or p.get("images") or []
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            try:
                raw = json.loads(s)
            except Exception:
                raw = [s]
        elif s:
            raw = [part for part in re.split(r"[,\n|]+", s) if part.strip()]
        else:
            raw = []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]

    out: List[str] = []
    seen: set = set()
    for cand in [main, *raw]:
        if isinstance(cand, dict):
            cand = cand.get("src") or cand.get("url") or ""
        u = str(cand or "").strip()
        if not u or u in seen or not _is_direct_image_url(u):
            continue
        seen.add(u)
        out.append(u)
    return out


# ── الكمية (افتراضي 100 إن غابت، أو القيمة الفعلية إن توفّرت) ──────────────
def _extract_qty(p: Dict, default: int = 100) -> int:
    q = _safe_float(p.get("الكمية", p.get("quantity", p.get("qty", 0))))
    return int(q) if q > 0 else default


# ── المكونات (نص): حقل صريح، وإلا يُبنى من النوتات العطرية ──────────────────
def _ingredients_text(p: Dict) -> str:
    explicit = str(p.get("المكونات") or p.get("ingredients") or "").strip()
    if explicit:
        return explicit
    top   = str(p.get("top_notes") or "").strip()
    heart = str(p.get("heart_notes") or p.get("middle_notes") or "").strip()
    base  = str(p.get("base_notes") or "").strip()
    parts = []
    if top:   parts.append(f"النفحات العليا: {top}")
    if heart: parts.append(f"القلب: {heart}")
    if base:  parts.append(f"القاعدة: {base}")
    return "؛ ".join(parts)


# ── المواصفات (نص): حقل صريح، وإلا يُبنى من الحجم/النوع/الجنس/العائلة ────────
def _specs_text(p: Dict) -> str:
    explicit = str(p.get("المواصفات") or p.get("specs") or "").strip()
    if explicit:
        return explicit
    size   = str(p.get("الحجم") or p.get("size") or "").strip()
    typ    = str(p.get("النوع") or p.get("type") or "").strip()
    gender = str(p.get("الجنس") or p.get("gender") or "").strip()
    family = str(p.get("العائلة_العطرية") or p.get("fragrance_family") or "").strip()
    parts = []
    if size:   parts.append(f"الحجم: {size}")
    if typ:    parts.append(f"النوع: {typ}")
    if gender: parts.append(f"الجنس: {gender}")
    if family: parts.append(f"العائلة العطرية: {family}")
    return "؛ ".join(parts)


def _seo_fields(p: Dict, name: str, brand: str) -> Dict[str, str]:
    """مفاتيح «عنوان السيو»/«وصف السيو» للحمولة (AI إن وُجد, وإلا حتمي).

    آمن: أي خطأ ⇒ حقول فارغة (يُنظّفها ``_nonempty``) فلا يُكسَر الإرسال."""
    try:
        from utils.seo_meta import seo_fields
        return seo_fields(p, name=name, brand=brand)
    except Exception as e:
        logger.warning("تعذّر توليد SEO — يُرسَل بلا عنوان/وصف سيو: %s", e)
        return {"عنوان السيو": "", "وصف السيو": ""}


def _seo_slug(name: str, brand: str) -> str:
    """slug إنجليزي لرابط صفحة المنتج (Brand_Name_Size). آمن: خطأ ⇒ ''."""
    try:
        from utils.seo_meta import build_seo_slug
        return build_seo_slug(name, brand)
    except Exception as e:
        logger.warning("تعذّر توليد رابط السيو: %s", e)
        return ""


# ── فرض قالب مهووس الذهبي الموحَّد على وصف كل منتج (يُرسَل مع كل منتج) ───────
def _apply_golden_description(products: List[Dict]) -> List[Dict]:
    """يستبدل وصف كل منتج بقالب مهووس الذهبي الموحَّد قبل الإرسال إلى Make.
    حتمي بلا API؛ يقرأ النوتات/الماركة/العائلة من حقول المنتج المُثراة، ويتدرّج
    تلقائياً للنسخة العامة عند غيابها. آمن: أي خطأ يُبقي الوصف الأصلي."""
    try:
        from engines.golden_template import build_golden_description  # type: ignore
    except Exception as e:
        logger.warning("تعذّر تحميل قالب مهووس الذهبي — يُستخدم الوصف الحالي: %s", e)
        return products
    for p in products:
        try:
            html = build_golden_description(p)
            if html:
                p["الوصف"] = html
                p["description"] = html
        except Exception as e:
            logger.debug("توليد الوصف الذهبي تعذّر لمنتج: %s", e)
    return products


# ── إسقاط الحقول الفارغة (نص فارغ / None / قائمة فارغة) من الـ payload ──────
def _nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) > 0
    return True


# ══════════════════════════════════════════════════════════════════════════
#  تحويل DataFrame → قائمة منتجات مع حساب السعر الصحيح لكل قسم
# ══════════════════════════════════════════════════════════════════════════
def export_to_make_format(df, section_type: str = "update") -> List[Dict]:
    """
    تحويل DataFrame إلى قائمة منتجات جاهزة لـ Make.
    section_type: raise | lower | approved | update | missing | new
    كل منتج يحتوي على: NO, product_id, name, price, section, + حقول سياقية
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return []

    products = []
    for _, row in df.iterrows():

        # ── رقم المنتج (NO = Primary Key في سلة/زد) ───────────────────────
        product_no = _extract_no(row)
        product_id = product_no or _clean_pid(
            row.get("معرف_المنتج")  or row.get("product_id")     or
            row.get("معرف المنتج")  or row.get("sku")            or
            row.get("SKU")          or ""
        )

        # ── اسم المنتج ────────────────────────────────────────────────────
        name = (
            str(row.get("المنتج",         "")) or
            str(row.get("منتج_المنافس",   "")) or
            str(row.get("أسم المنتج",     "")) or
            str(row.get("اسم المنتج",     "")) or
            str(row.get("name",           "")) or ""
        ).strip()
        if name in ("", "nan", "None"): name = ""

        # ── السعر حسب القسم ───────────────────────────────────────────────
        comp_price = _safe_float(row.get("سعر_المنافس", 0))
        our_price  = _safe_float(
            row.get("السعر", 0) or row.get("سعر المنتج", 0) or
            row.get("price",  0) or 0
        )

        if section_type == "raise":
            # سعرنا أقل من المنافس → نرفع سعرنا ليكون أقل بـ 1 ريال من المنافس
            price = round(comp_price - 1, 2) if comp_price > 0 else our_price
        elif section_type == "lower":
            # سعرنا أعلى من المنافس → نخفض سعرنا ليكون أقل بـ 1 ريال من المنافس
            price = round(comp_price - 1, 2) if comp_price > 0 else our_price
        elif section_type in ("approved", "update"):
            price = our_price
        else:
            price = comp_price if comp_price > 0 else our_price

        if not name: continue

        comp_name  = str(row.get("منتج_المنافس", ""))
        comp_src   = str(row.get("المنافس", ""))
        diff       = _safe_float(row.get("الفرق", 0))
        match_pct  = _safe_float(row.get("نسبة_التطابق", 0))
        decision   = str(row.get("القرار", ""))
        brand      = str(row.get("الماركة", ""))

        product = {
            "NO":         product_no,          # ← Primary Key في سلة/زد
            "product_id": product_id,
            "name":       name,
            "price":      float(price),
            "section":    section_type,
        }

        if comp_name and comp_name not in ("nan", "None", "—"):
            product["comp_name"] = comp_name
        if comp_src and comp_src not in ("nan", "None"):
            product["competitor"] = comp_src
        if diff:
            product["price_diff"] = diff
        if match_pct:
            product["match_score"] = match_pct
        if decision and decision not in ("nan", "None"):
            product["decision"] = decision
        if brand and brand not in ("nan", "None"):
            product["brand"] = brand

        products.append(product)

    return products


# ══════════════════════════════════════════════════════════════════════════
#  إرسال منتج واحد — تحديث السعر
#  Payload: {"products": [{"NO":"...","product_id":"...","name":"...","price":...}]}
# ══════════════════════════════════════════════════════════════════════════
def send_single_product(product: Dict) -> Dict:
    """
    إرسال منتج واحد لتحديث سعره في سلة عبر Make.
    Make يقرأ: {{2.products}} → NO | product_id | name | price
    Payload: {"products": [{...}]}
    """
    if not product:
        return {"success": False, "message": "❌ لا توجد بيانات للإرسال"}

    name       = str(product.get("name", "")).strip()
    price      = _safe_float(product.get("price", 0))
    product_no = _extract_no(product) or _clean_pid(product.get("NO", ""))
    product_id = product_no or _clean_pid(product.get("product_id", ""))

    if not name:
        return {"success": False, "message": "❌ اسم المنتج مطلوب"}
    if price <= 0:
        return {"success": False, "message": f"❌ السعر غير صحيح: {price}"}

    pid_int = _pid_as_int(product_no or product_id)
    if pid_int is None:
        logger.warning("⚠️ NO/product_id غير رقمي للمنتج «%s» — سيُرسل كنص", name[:50])
        pid_int = product_no or product_id or ""
    _prod = {
        "NO":          product_no or product_id,     # ← Primary Key Make (fallback صلب)
        "product_id":  pid_int,                       # ← integer لموديول Salla
        "name":        name,
        "price":       float(price),
        "section":     product.get("section", "update"),
        "comp_name":   product.get("comp_name", ""),
        "competitor":  product.get("competitor", ""),
        "price_diff":  product.get("price_diff", product.get("diff", 0)),
        "match_score": product.get("match_score", 0),
        "decision":    product.get("decision", ""),
        "brand":       product.get("brand", ""),
    }
    _cu = str(product.get("comp_url", product.get("رابط_المنافس", "")) or "").strip()
    if _cu:
        _prod["comp_url"] = _cu

    payload = {"products": [_prod]}

    result = _post_to_webhook(WEBHOOK_UPDATE_PRICES, payload)
    if result["success"]:
        id_info = f" [NO: {product_no}]" if product_no else (f" [ID: {product_id}]" if product_id else "")
        result["message"] = f"✅ تم تحديث «{name}»{id_info} ← {price:,.0f} ر.س"
    return result


def trigger_price_update(
    sku: str,
    target_price: float,
    comp_url: str = "",
    *,
    name: str = "",
    comp_name: str = "",
    comp_price: float = 0.0,
    diff: float = 0.0,
    decision: str = "",
    competitor: str = "",
    no: str = "",
) -> bool:
    """
    غلاف تفاعلي لإرسال تحديث سعر واحد إلى Make.com.
    يعيد True عند نجاح HTTP. الحقل `no` هو رقم المنتج في كتالوج سلة/زد.
    """
    res = send_single_product({
        "NO":         no or sku,
        "product_id": sku,
        "name": name,
        "price": float(target_price),
        "comp_name": comp_name,
        "comp_price": comp_price,
        "diff": diff,
        "decision": decision,
        "competitor": competitor,
        "comp_url": comp_url or "",
    })
    return bool(res.get("success"))


# ══════════════════════════════════════════════════════════════════════════
#  إرسال عدة منتجات — تحديث الأسعار
#  Payload: {"products": [{NO, product_id, name, price, ...}]}
# ══════════════════════════════════════════════════════════════════════════
def send_price_updates(products: List[Dict]) -> Dict:
    """
    إرسال قائمة منتجات لتحديث أسعارها في سلة عبر Make.
    كل عنصر يحتوي على `NO` (رقم منتج سلة/زد) لضمان التحديث الدقيق.
    """
    if not products:
        return {"success": False, "message": "❌ لا توجد منتجات للإرسال"}

    valid_products = []
    skipped = 0

    for p in products:
        name       = str(p.get("name", "")).strip()
        price      = _safe_float(p.get("price", 0))
        product_no = _extract_no(p) or _clean_pid(p.get("NO", ""))
        product_id = product_no or _clean_pid(p.get("product_id", ""))

        if not name or price <= 0:
            skipped += 1
            continue

        pid_int = _pid_as_int(product_no or product_id)
        if pid_int is None:
            logger.warning("⚠️ تخطي «%s» — product_id غير رقمي", name[:50])
            skipped += 1
            continue

        if not product_no:
            logger.warning("⚠️ NO فارغ في الدفعة عند «%s»", name[:50])
        valid_products.append({
            "NO":          product_no or product_id,      # ← Primary Key Make (fallback صلب)
            "product_id":  pid_int,                        # ← integer لموديول Salla
            "name":        name,
            "price":       float(price),
            "section":     p.get("section", "update"),
            "comp_name":   p.get("comp_name", ""),
            "competitor":  p.get("competitor", ""),
            "price_diff":  p.get("price_diff", p.get("diff", 0)),
            "match_score": p.get("match_score", 0),
            "decision":    p.get("decision", ""),
            "brand":       p.get("brand", ""),
        })

    if not valid_products:
        return {
            "success": False,
            "message": f"❌ لا توجد منتجات صالحة (تم تخطي {skipped} منتج)"
        }

    payload = {"products": valid_products}
    _no_count = sum(1 for p in valid_products if p.get("NO"))
    logger.info("📤 إرسال %d منتج إلى Make — مع NO: %d/%d",
                len(valid_products), _no_count, len(valid_products))
    result = _post_to_webhook(WEBHOOK_UPDATE_PRICES, payload)

    # ── أرضية خطأ الكشط (أ2): استبعاد ما حجزه الظلّ قبل التدوين — لا سعر محجوز
    # يدخل send_log كـ«مُرسَل» (المقياس الرقمي للوحدة يعتمد على نظافة send_log).
    def _item_key(_d: dict) -> str:
        return str(_d.get("NO") or _d.get("product_id") or "")

    _blocked_keys = {
        k for _b in result.get("blocked_low_price", []) if (k := _item_key(_b))
    }
    _sent_products = [
        p for p in valid_products
        if not (_item_key(p) and _item_key(p) in _blocked_keys)
    ]

    # ── تدوين الإرسال (صف لكل منتج — العمود الفقري للتغذية الراجعة) ────────
    try:
        from utils.send_log import log_send
        for _vp in _sent_products:
            log_send("update",
                     sku=str(_vp.get("NO") or _vp.get("product_id") or ""),
                     product_name=str(_vp.get("name", "")),
                     price=_vp.get("price"),
                     http_status=result.get("status_code", 0),
                     success=result.get("success", False),
                     response_excerpt=result.get("message", ""))
        for _bp in result.get("blocked_low_price", []):
            log_send("update",
                     sku=str(_bp.get("NO") or _bp.get("product_id") or ""),
                     product_name=str(_bp.get("name", "")),
                     success=False,
                     response_excerpt=str(_bp.get("blocked_reason", "blocked_low_price"))[:200])
    except Exception as _e:
        logger.warning("تعذّر تدوين إرسال الأسعار: %s", _e)

    if result["success"]:
        skip_msg = f" (تم تخطي {skipped})" if skipped else ""
        with_no = sum(1 for p in valid_products if p.get("NO"))
        no_msg = f" | مع NO: {with_no}/{len(valid_products)}"
        result["message"] = f"✅ تم إرسال {len(valid_products)} منتج لتحديث الأسعار{no_msg}{skip_msg}"
    return result


# ══════════════════════════════════════════════════════════════════════════
#  صدق حالة الإرسال (دالة خالصة — بلا POST، قابلة للاختبار مباشرة)
# ══════════════════════════════════════════════════════════════════════════
def build_new_send_outcome(
    sent: int, skipped: int, failed: int, total: int,
    first_error: Optional[Dict] = None,
) -> Dict:
    """يبني نتيجة إرسال المنتجات الجديدة **بصدق**:
    • ``sent>0``                        ⇒ نجاح (كامل/جزئي).
    • ``sent==0, skipped>0, failed==0`` ⇒ **معلومة** لا فشل: كلّه مكرّر أُرسل سابقاً.
    • ``sent==0, failed>0``             ⇒ فشل فعليّ مع **أول سبب** (كود HTTP/الاستثناء).
    """
    if sent > 0:
        skip_msg = f" (تم تخطي {skipped})" if skipped else ""
        err_msg = f" (فشل {failed})" if failed else ""
        return {
            "success": True, "level": "success",
            "message": f"✅ تم إرسال {sent} منتج جديد إلى Make{skip_msg}{err_msg}",
            "sent": sent, "failed": failed, "skipped": skipped, "total": total,
            "status_code": 200 if failed == 0 else 207,
        }
    if failed == 0 and skipped > 0:
        return {
            "success": True, "level": "info",
            "message": f"ℹ️ لا جديد للإرسال — {skipped} أُرسل سابقاً (مكرر)",
            "sent": 0, "failed": 0, "skipped": skipped, "total": total,
            "status_code": 0,
        }
    _cause = str((first_error or {}).get("message", "") or "").strip()
    _cause_txt = f" — أول سبب: {_cause}" if _cause else ""
    return {
        "success": False, "level": "error",
        "message": f"❌ فشل إرسال جميع المنتجات ({failed} فشل){_cause_txt}. تم تخطي {skipped}",
        "sent": 0, "failed": failed, "skipped": skipped, "total": total,
        "status_code": int((first_error or {}).get("status_code", 0) or 0),
    }


# ══════════════════════════════════════════════════════════════════════════
#  إرسال منتجات جديدة — Webhook منفصل
#  Payload: {"data": [{NO, أسم المنتج, سعر المنتج, ...}]}
# ══════════════════════════════════════════════════════════════════════════
def send_new_products(products: List[Dict]) -> Dict:
    if not products:
        return {"success": False, "message": "❌ لا توجد منتجات للإرسال"}

    # ── قالب مهووس الذهبي الموحَّد: يُرفَق بوصف كل منتج قبل البوابة ────────
    products = _apply_golden_description(products)

    # ── بوابة إلزامية: وصف مهووس + رابط صورة حقيقي ────────────────────
    # FIX: إذا الوصف جاء جاهزاً من المحرك التنفيذي لا نعيد توليده
    try:
        from utils.product_gate import validate_and_enrich, is_mahwous_description
        _has_desc = any(
            is_mahwous_description(p.get("الوصف") or p.get("description") or "")
            for p in products
        )
        products, _gate_rejected = validate_and_enrich(
            products, auto_generate_desc=not _has_desc,
        )
        gate_skipped = len(_gate_rejected)
        if gate_skipped:
            logger.warning("🚫 بوابة الجودة استبعدت %d منتج (وصف/صورة مفقود)", gate_skipped)
    except Exception as _e:
        logger.error("فشل تطبيق بوابة الجودة: %s", _e)
        gate_skipped = 0
    if not products:
        return {"success": False,
                "message": f"❌ لا توجد منتجات صالحة — تم رفض {gate_skipped} لغياب وصف مهووس أو صورة حقيقية"}

    sent, skipped, errors = 0, gate_skipped, []
    first_error: Optional[Dict] = None

    for p in products:
        name  = str(p.get("name", p.get("أسم المنتج", ""))).strip()
        price = _safe_float(
            p.get("price", 0) or p.get("سعر المنتج", 0) or p.get("السعر", 0)
        )
        product_no = _extract_no(p) or _clean_pid(p.get("NO", ""))
        pid = product_no or _clean_pid(p.get("product_id", p.get("معرف_المنتج", "")))

        if not name:
            skipped += 1
            continue

        # ── حارس التكرار: SKU مُرسَل بنجاح كجديد سابقاً أو موجود في our_catalog ──
        _sku = str(p.get("sku", p.get("رمز المنتج sku", ""))).strip()
        try:
            from utils.send_log import is_duplicate_new_sku, log_send
            if _sku and is_duplicate_new_sku(_sku):
                log_send("new", sku=_sku, product_name=name,
                         response_excerpt="skipped_duplicate")
                skipped += 1
                continue
        except Exception as _e:
            logger.warning("تعذّر فحص تكرار «%s»: %s", name[:40], _e)

        image_urls = _collect_image_urls(p)
        _brand = _brand_name(p)
        _seo = _seo_fields(p, name, _brand)
        item = {
            "NO":              product_no,                # ← Primary Key Make
            "product_id":      pid,
            "أسم المنتج":      name,
            "سعر المنتج":      float(price),
            "رمز المنتج sku":  _sku,
            "الوزن":           int(_safe_float(p.get("weight", p.get("الوزن", 1))) or 1),
            "سعر التكلفة":     float(_safe_float(p.get("cost_price", p.get("سعر التكلفة", 0)))),
            "السعر المخفض":    float(_safe_float(p.get("sale_price",  p.get("السعر المخفض", 0)))),
            "الوصف":           str(p.get("الوصف", p.get("description", ""))).strip(),
            "صورة المنتج":     image_urls[0] if image_urls else "",   # الرئيسية (توافق خلفي)
            "صور المنتج":      image_urls,                  # ← مصفوفة كل الصور (Iterator في Make)
            "الماركة":         _brand,                      # ← دائماً نصّياً (Make يُنشئها عند الغياب)
            "صورة شعار الماركة": _brand_logo(p, _brand),    # ← شعار لإنشاء ماركة جديدة في سلة
            "brand_id":        _resolve_brand_id(p),
            "اسم التصنيف":     _category_name(p),           # ← دائماً نصّياً
            "category_id":     _resolve_category_id(p),
            "الكمية":          _extract_qty(p),
            "المكونات":        _ingredients_text(p),
            "المواصفات":       _specs_text(p),
            "عنوان السيو":     _seo["عنوان السيو"],         # ← SEO metadata_title (≤60)
            "وصف السيو":       _seo["وصف السيو"],           # ← SEO metadata_description (≤160)
            "رابط السيو":      _seo_slug(name, _brand),     # ← slug إنجليزي لصفحة المنتج
        }
        # تنظيف: إزالة الحقول الفارغة (نص فارغ / None / قائمة فارغة)
        item = {k: v for k, v in item.items() if _nonempty(v)}

        result = _post_to_webhook(WEBHOOK_NEW_PRODUCTS, {"data": [item]})
        if result["success"]:
            sent += 1
        else:
            errors.append(name)
            if first_error is None:
                first_error = result

        # ── تدوين الإرسال بعد ردّ الويبهوك — لا يمنع الإرسال إن فشل ──────────
        try:
            from utils.send_log import log_send
            log_send("new", sku=_sku, product_name=name, brand=_brand,
                     category=_category_name(p),
                     price=price,
                     comp_price=_safe_float(p.get("سعر_المنافس", 0)) or None,
                     http_status=result.get("status_code", 0),
                     success=result.get("success", False),
                     response_excerpt=f"[{result.get('state', '?')}] {result.get('message', '')}")
        except Exception as _e:
            logger.warning("تعذّر تدوين إرسال «%s»: %s", name[:40], _e)

        if len(products) > 1:
            time.sleep(0.3)

    return build_new_send_outcome(sent, skipped, len(errors), len(products), first_error)


# ══════════════════════════════════════════════════════════════════════════
#  إرسال المنتجات المفقودة — نفس سيناريو المنتجات الجديدة
# ══════════════════════════════════════════════════════════════════════════
def send_missing_products(products: List[Dict]) -> Dict:
    if not products:
        return {"success": False, "message": "❌ لا توجد منتجات مفقودة للإرسال"}

    # ── قالب مهووس الذهبي الموحَّد: يُرفَق بوصف كل منتج قبل البوابة ────────
    products = _apply_golden_description(products)

    # ── بوابة إلزامية: وصف مهووس + رابط صورة حقيقي ────────────────────
    # FIX: إذا الوصف جاء جاهزاً من المحرك التنفيذي لا نعيد توليده
    try:
        from utils.product_gate import validate_and_enrich, is_mahwous_description
        _has_desc = any(
            is_mahwous_description(p.get("الوصف") or p.get("description") or "")
            for p in products
        )
        products, _gate_rejected = validate_and_enrich(
            products, auto_generate_desc=not _has_desc,
        )
        gate_skipped = len(_gate_rejected)
        if gate_skipped:
            logger.warning("🚫 بوابة الجودة استبعدت %d منتج مفقود (وصف/صورة مفقود)", gate_skipped)
    except Exception as _e:
        logger.error("فشل تطبيق بوابة الجودة: %s", _e)
        gate_skipped = 0
    if not products:
        return {"success": False,
                "message": f"❌ لا توجد منتجات مفقودة صالحة — رفض {gate_skipped} لغياب وصف مهووس أو صورة حقيقية"}

    sent, skipped, errors = 0, gate_skipped, []

    for p in products:
        name  = str(p.get("name", p.get("المنتج", p.get("منتج_المنافس", "")))).strip()
        comp_price = _safe_float(
            p.get("سعر_المنافس", 0) or p.get("comp_price", 0) or p.get("competitor_price", 0)
        )
        # قاعدة التسعير للمفقودات: سعر المنافس − 1
        if comp_price > 0:
            price = max(int(round(comp_price - 1)), 1)
        else:
            price = int(round(_safe_float(p.get("price", 0) or p.get("السعر", 0))))
        product_no = _extract_no(p) or _clean_pid(p.get("NO", ""))
        pid = product_no or _clean_pid(p.get("product_id", p.get("معرف_المنتج", "")))

        if not name or price <= 0:
            skipped += 1
            continue

        image_urls = _collect_image_urls(p)
        _brand = _brand_name(p)
        _seo = _seo_fields(p, name, _brand)
        item = {
            "NO":              product_no,                # ← Primary Key Make
            "product_id":      pid,
            "أسم المنتج":      name,
            "سعر المنتج":      price,                      # uinteger لـ Salla
            "رمز المنتج sku":  str(p.get("sku", p.get("رمز المنتج sku", ""))).strip(),
            "الوزن":           1,                          # ثابت حسب القاعدة
            "سعر التكلفة":     int(round(_safe_float(p.get("cost_price", p.get("سعر التكلفة", 0))))),
            "السعر المخفض":    int(round(_safe_float(p.get("sale_price",  p.get("السعر المخفض", 0))))),
            "الوصف":           str(p.get("الوصف", p.get("description", ""))).strip(),
            "صورة المنتج":     image_urls[0] if image_urls else "",   # الرئيسية (توافق خلفي)
            "صور المنتج":      image_urls,                  # ← مصفوفة كل الصور (Iterator في Make)
            "الماركة":         _brand,                      # ← دائماً نصّياً (Make يُنشئها عند الغياب)
            "صورة شعار الماركة": _brand_logo(p, _brand),    # ← شعار لإنشاء ماركة جديدة في سلة
            "brand_id":        _resolve_brand_id(p),
            "اسم التصنيف":     _category_name(p),           # ← دائماً نصّياً
            "category_id":     _resolve_category_id(p),
            "الكمية":          _extract_qty(p),
            "المكونات":        _ingredients_text(p),
            "المواصفات":       _specs_text(p),
            "عنوان السيو":     _seo["عنوان السيو"],         # ← SEO metadata_title (≤60)
            "وصف السيو":       _seo["وصف السيو"],           # ← SEO metadata_description (≤160)
            "رابط السيو":      _seo_slug(name, _brand),     # ← slug إنجليزي لصفحة المنتج
        }
        # تنظيف: إزالة الحقول الفارغة (نص فارغ / None / قائمة فارغة)
        item = {k: v for k, v in item.items() if _nonempty(v)}

        result = _post_to_webhook(WEBHOOK_NEW_PRODUCTS, {"data": [item]})
        if result["success"]:
            sent += 1
        else:
            errors.append(name)

        # ── تدوين الإرسال (السعر المُرسَل + سعر المنافس المرجع) — لا يمنع الإرسال ──
        try:
            from utils.send_log import log_send
            log_send("new", sku=str(p.get("sku", p.get("رمز المنتج sku", ""))).strip(),
                     product_name=name, brand=_brand, category=_category_name(p),
                     price=price, comp_price=comp_price or None,
                     http_status=result.get("status_code", 0),
                     success=result.get("success", False),
                     response_excerpt=result.get("message", ""))
        except Exception as _e:
            logger.warning("تعذّر تدوين إرسال مفقود «%s»: %s", name[:40], _e)

        if len(products) > 1:
            time.sleep(0.3)

    failed = len(errors)
    if sent == 0:
        return {
            "success": False,
            "message": f"❌ فشل إرسال جميع المنتجات المفقودة. تم تخطي {skipped}",
            "sent": sent,
            "failed": failed,
            "total": len(products),
            "status_code": 0,
        }

    skip_msg = f" (تم تخطي {skipped})" if skipped else ""
    err_msg  = f" (فشل {len(errors)})" if errors else ""
    return {
        "success": sent > 0,
        "message": f"✅ تم إرسال {sent} منتج مفقود إلى Make{skip_msg}{err_msg}",
        "sent": sent,
        "failed": failed,
        "total": len(products),
        "status_code": 200 if failed == 0 else 207,
    }


# ══════════════════════════════════════════════════════════════════════════
#  إرسال بدفعات ذكية مع retry و progress callback
# ══════════════════════════════════════════════════════════════════════════
def send_batch_smart(products: list, batch_type: str = "update",
                     batch_size: int = 20, max_retries: int = 3,
                     progress_cb=None, confidence_filter: str = "") -> Dict:
    if not products:
        return {"success": False, "message": "❌ لا توجد منتجات للإرسال",
                "sent": 0, "failed": 0, "total": 0, "errors": []}

    if confidence_filter:
        products = [p for p in products
                    if p.get("مستوى_الثقة", "green") == confidence_filter
                    or p.get("confidence_level", "green") == confidence_filter]

    total = len(products)
    if total == 0:
        return {"success": False, "message": "❌ لا توجد منتجات بهذا المستوى من الثقة",
                "sent": 0, "failed": 0, "total": 0, "errors": []}

    sent_count = 0
    fail_count = 0
    error_names = []
    last_fail_msg = ""   # سبب آخر فشل (يُسرَّب للأعلى بدل «فشل N» الغامض)

    for i in range(0, total, batch_size):
        batch = products[i:i + batch_size]

        for attempt in range(1, max_retries + 1):
            try:
                if batch_type == "update":
                    result = send_price_updates(batch)
                else:
                    result = send_new_products(batch)

                if result["success"]:
                    sent_count += len(batch)
                    break
                elif attempt < max_retries:
                    time.sleep(2 * attempt)
                    continue
                else:
                    fail_count += len(batch)
                    last_fail_msg = str(result.get("message", "") or "")
                    error_names.extend([p.get("name", p.get("منتج_المنافس", "?"))[:30] for p in batch])
            except Exception as exc:
                if attempt >= max_retries:
                    fail_count += len(batch)
                    last_fail_msg = f"استثناء: {exc}"
                    error_names.extend([p.get("name", "?")[:30] for p in batch])
                else:
                    time.sleep(2 * attempt)

        if progress_cb:
            try:
                progress_cb(sent_count, fail_count, total,
                           batch[-1].get("name", "")[:30] if batch else "")
            except Exception:
                pass

        if i + batch_size < total:
            time.sleep(0.5)

    success = sent_count > 0
    msg_parts = []
    if sent_count > 0:
        msg_parts.append(f"✅ نجح {sent_count}")
    if fail_count > 0:
        msg_parts.append(f"❌ فشل {fail_count}")
    msg = f"إرسال {total} منتج: {' | '.join(msg_parts)}"
    # سرِّب سبب الفشل الفعلي (صورة/وصف/ويبهوك) بدل «فشل N» الغامض
    if fail_count > 0 and last_fail_msg:
        msg += f" — السبب: {last_fail_msg}"

    return {
        "success":  success,
        "message":  msg,
        "sent":     sent_count,
        "failed":   fail_count,
        "total":    total,
        "errors":   error_names[:20],
        "fail_reason": last_fail_msg,
    }


# ══════════════════════════════════════════════════════════════════════════
#  فحص حالة الاتصال بـ Webhooks
# ══════════════════════════════════════════════════════════════════════════
def verify_webhook_connection() -> Dict:
    test_price_payload = {
        "products": [{
            "NO":         "1",
            "product_id": 1,
            "name":       "اختبار الاتصال",
            "price":      1.0,
            "section":    "test",
        }]
    }
    r1 = _post_to_webhook(WEBHOOK_UPDATE_PRICES, test_price_payload)

    test_new_payload = {
        "data": [{
            "NO":             "",
            "product_id":     "",
            "أسم المنتج":     "اختبار الاتصال",
            "سعر المنتج":     1.0,
            "رمز المنتج sku": "",
            "الوزن":          1,
            "سعر التكلفة":    0,
            "السعر المخفض":   0,
            "الوصف":          "test",
        }]
    }
    r2 = _post_to_webhook(WEBHOOK_NEW_PRODUCTS, test_new_payload)

    return {
        "update_prices": {
            "success": r1["success"],
            "message": r1["message"],
            "url": WEBHOOK_UPDATE_PRICES[:55] + "..." if len(WEBHOOK_UPDATE_PRICES) > 55 else WEBHOOK_UPDATE_PRICES,
        },
        "new_products": {
            "success": r2["success"],
            "message": r2["message"],
            "url": WEBHOOK_NEW_PRODUCTS[:55] + "..." if len(WEBHOOK_NEW_PRODUCTS) > 55 else WEBHOOK_NEW_PRODUCTS,
        },
        "all_connected": r1["success"] and r2["success"],
    }


# ══════════════════════════════════════════════════════════════════════════
#  تصدير ملفات سلة للرفع اليدوي (بديل عن Webhook)
# ══════════════════════════════════════════════════════════════════════════

# أعمدة ملف استيراد المنتجات في سلة (من قالب «منتج جديد.csv»)
SALLA_PRODUCT_COLUMNS = [
    "النوع ", "أسم المنتج", "تصنيف المنتج", "صورة المنتج", "وصف صورة المنتج",
    "نوع المنتج", "سعر المنتج", "الوصف", "هل يتطلب شحن؟", "رمز المنتج sku",
    "سعر التكلفة", "السعر المخفض", "تاريخ بداية التخفيض", "تاريخ نهاية التخفيض",
    "اقصي كمية لكل عميل", "إخفاء خيار تحديد الكمية", "اضافة صورة عند الطلب",
    "الوزن", "وحدة الوزن", "الماركة", "العنوان الترويجي", "تثبيت المنتج",
    "الباركود", "السعرات الحرارية", "MPN", "GTIN", "خاضع للضريبة ؟",
    "سبب عدم الخضوع للضريبة",
]

# أعمدة ملف استيراد الماركات في سلة (من قالب «ماركات مهووس.csv»)
SALLA_BRAND_COLUMNS = [
    "اسم الماركة", "وصف مختصر عن الماركة", "صورة شعار الماركة",
    "(إختياري) صورة البانر", "(Page Title) عنوان صفحة العلامة التجارية",
    "(SEO Page URL) رابط صفحة العلامة التجارية",
    "(Page Description) وصف صفحة العلامة التجارية",
]


def export_missing_products_to_salla_csv(products: List[Dict], output_path: str) -> Dict:
    """
    تصدير المنتجات المفقودة إلى ملف CSV بصيغة قالب استيراد منتجات سلة.
    للرفع اليدوي في لوحة تحكم سلة → إدارة المنتجات → استيراد.

    السعر = سعر المنافس − 1 | الوزن = 1 | الكمية الافتراضية = 100
    """
    import csv

    if not products:
        return {"success": False, "message": "❌ لا توجد منتجات للتصدير", "path": ""}

    # ── بوابة إلزامية: وصف مهووس + صورة حقيقية ────────────────────────
    try:
        from utils.product_gate import validate_and_enrich
        products, _gate_rejected = validate_and_enrich(list(products), auto_generate_desc=True)
        _gate_skipped = len(_gate_rejected)
        if _gate_skipped:
            logger.warning("🚫 بوابة التصدير: رفض %d منتج (وصف/صورة مفقود)", _gate_skipped)
    except Exception as _e:
        logger.error("فشل بوابة التصدير: %s", _e)
        _gate_skipped = 0
    if not products:
        return {"success": False,
                "message": f"❌ لا منتجات صالحة للتصدير — رُفض {_gate_skipped} لغياب وصف مهووس أو صورة حقيقية",
                "path": ""}

    rows = []
    for p in products:
        name = str(p.get("name", p.get("المنتج", p.get("منتج_المنافس", "")))).strip()
        comp_price = _safe_float(
            p.get("سعر_المنافس", 0) or p.get("comp_price", 0) or p.get("competitor_price", 0)
        )
        price = max(int(round(comp_price - 1)), 1) if comp_price > 0 else int(
            round(_safe_float(p.get("price", 0) or p.get("السعر", 0)))
        )
        if not name or price <= 0:
            continue

        row = {col: "" for col in SALLA_PRODUCT_COLUMNS}
        row["النوع "]              = "منتج"
        row["أسم المنتج"]          = name
        row["تصنيف المنتج"]        = str(p.get("category_name", p.get("التصنيف", "")))
        row["صورة المنتج"]         = str(p.get("image_url", p.get("صورة المنتج", "")))
        row["وصف صورة المنتج"]     = f"زجاجة {name}"
        row["نوع المنتج"]          = "منتج جاهز"
        row["سعر المنتج"]          = price
        row["الوصف"]               = str(p.get("الوصف", p.get("description", "")))
        row["هل يتطلب شحن؟"]       = "نعم"
        row["رمز المنتج sku"]      = str(p.get("sku", p.get("رمز المنتج sku", "")))
        row["سعر التكلفة"]         = int(round(_safe_float(p.get("cost_price", 0))))
        row["السعر المخفض"]        = int(round(_safe_float(p.get("sale_price", 0))))
        row["الوزن"]               = 1
        row["وحدة الوزن"]          = "كجم"
        row["الماركة"]             = str(p.get("brand", p.get("الماركة", "")))
        row["إخفاء خيار تحديد الكمية"] = "لا"
        row["تثبيت المنتج"]        = "لا"
        row["خاضع للضريبة ؟"]      = "نعم"
        rows.append(row)

    if not rows:
        return {"success": False, "message": "❌ لا توجد منتجات صالحة للتصدير", "path": ""}

    try:
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SALLA_PRODUCT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return {
            "success": True,
            "message": f"✅ تم تصدير {len(rows)} منتج إلى ملف سلة",
            "path": output_path,
            "count": len(rows),
        }
    except Exception as e:
        return {"success": False, "message": f"❌ فشل التصدير: {e}", "path": ""}


def export_missing_brands_to_salla_csv(
    brands: List[Dict], existing_brands: List[str], output_path: str
) -> Dict:
    """
    تصدير الماركات المفقودة إلى ملف CSV بصيغة قالب استيراد ماركات سلة.
    للرفع اليدوي → لوحة سلة → الماركات → استيراد.

    brands: قائمة dicts فيها 'name' و 'description' و 'logo_url' (اختيارية)
    existing_brands: قائمة أسماء الماركات الموجودة (للاستثناء)
    """
    import csv

    if not brands:
        return {"success": False, "message": "❌ لا توجد ماركات للتصدير", "path": ""}

    existing_norm = {str(b).strip().lower() for b in (existing_brands or []) if b}
    rows = []
    seen = set()

    for b in brands:
        name = str(b.get("name", b.get("brand", b.get("الماركة", "")))).strip()
        if not name:
            continue
        key = name.lower()
        if key in existing_norm or key in seen:
            continue
        seen.add(key)

        row = {col: "" for col in SALLA_BRAND_COLUMNS}
        row["اسم الماركة"]                                      = name
        row["وصف مختصر عن الماركة"]                             = str(
            b.get("description", f"ماركة {name} - متوفرة في مهووس للعطور")
        )
        row["صورة شعار الماركة"]                                = str(b.get("logo_url", ""))
        row["(Page Title) عنوان صفحة العلامة التجارية"]         = f"{name} | عطور فاخرة - مهووس"
        row["(SEO Page URL) رابط صفحة العلامة التجارية"]        = f"ماركة-{name.replace(' ', '-')}"
        row["(Page Description) وصف صفحة العلامة التجارية"]     = (
            f"اكتشف تشكيلة {name} الفاخرة في مهووس للعطور. عطور أصلية بأفضل الأسعار."
        )
        rows.append(row)

    if not rows:
        return {"success": False, "message": "ℹ️ كل الماركات موجودة — لا شيء للتصدير", "path": ""}

    try:
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SALLA_BRAND_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return {
            "success": True,
            "message": f"✅ تم تصدير {len(rows)} ماركة مفقودة",
            "path": output_path,
            "count": len(rows),
        }
    except Exception as e:
        return {"success": False, "message": f"❌ فشل التصدير: {e}", "path": ""}
