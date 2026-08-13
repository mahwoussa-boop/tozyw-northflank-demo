"""services/export_service.py — تصدير Make.com وSalla وCSV/Excel (نقل حرفي).

- Make.com: حمولة ``{NO, product_id, name, price, section, +سياق}`` بنفس مفاتيح
  ``make_helper.export_to_make_format`` (المفاتيح يعتمدها Make — ممنوع تغييرها).
- Salla: قائمة الأعمدة الأربعين بالترتيب الحرفي + التفاف على المُولّد القانوني
  (``salla_shamel_export`` يستورد Streamlit ⇒ يُستورَد كسولاً عند الاستدعاء فقط).
- CSV/Excel: توليد قياسي عبر pandas.

#PRESERVED_LOGIC: export_to_make_format (make_helper.py:169-249)،
SALLA_SHAMEL_COLUMNS (salla_shamel_export.py:28-69).
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional

import pandas as pd

from conf.constants import PROJECT_ROOT
from core.exceptions import ExportError

# 40 عمود سلة بالترتيب الحرفي (لاحظ المسافة اللاحقة في "النوع ").
SALLA_SHAMEL_COLUMNS: list[str] = [
    "النوع ", "أسم المنتج", "تصنيف المنتج", "صورة المنتج", "وصف صورة المنتج",
    "نوع المنتج", "سعر المنتج", "الوصف", "هل يتطلب شحن؟", "رمز المنتج sku",
    "سعر التكلفة", "السعر المخفض", "تاريخ بداية التخفيض", "تاريخ نهاية التخفيض",
    "اقصي كمية لكل عميل", "إخفاء خيار تحديد الكمية", "اضافة صورة عند الطلب",
    "الوزن", "وحدة الوزن", "الماركة", "العنوان الترويجي", "تثبيت المنتج",
    "الباركود", "السعرات الحرارية", "MPN", "GTIN", "خاضع للضريبة ؟",
    "سبب عدم الخضوع للضريبة",
    "[1] الاسم", "[1] النوع", "[1] القيمة", "[1] الصورة / اللون",
    "[2] الاسم", "[2] النوع", "[2] القيمة", "[2] الصورة / اللون",
    "[3] الاسم", "[3] النوع", "[3] القيمة", "[3] الصورة / اللون",
]


def _safe_float(val: Any, default: float = 0.0) -> float:
    """#PRESERVED_LOGIC make_helper.py:72-78."""
    try:
        if val is None or str(val).strip() in ("", "nan", "None", "NaN"):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _clean_pid(raw: Any) -> str:
    """product_id كـ ``str(int(float(value)))``. #PRESERVED_LOGIC make_helper.py:82-90."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if text in ("", "nan", "None", "NaN", "0", "0.0"):
        return ""
    try:
        return str(int(float(text)))
    except (ValueError, TypeError):
        return text


def _extract_no(row: Any) -> str:
    """رقم المنتج (Primary Key سلة/زد). #PRESERVED_LOGIC make_helper.py:105-120."""
    if row is None:
        return ""
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    raw = (
        getter("No.") or getter("NO") or getter("no") or getter("No")
        or getter("رقم_المنتج") or getter("رقم المنتج")
        or getter("catalog_no") or getter("product_no") or ""
    )
    return _clean_pid(raw)


def _section_price(section_type: str, our_price: float, comp_price: float) -> float:
    """السعر حسب القسم. #PRESERVED_LOGIC make_helper.py:206-215."""
    if section_type in ("raise", "lower"):
        return round(comp_price - 1, 2) if comp_price > 0 else our_price
    if section_type in ("approved", "update"):
        return our_price
    return comp_price if comp_price > 0 else our_price


def _add_context(product: dict[str, Any], row: Any) -> None:
    """يضيف الحقول السياقية المشروطة. #PRESERVED_LOGIC make_helper.py:234-245."""
    comp_name = str(row.get("منتج_المنافس", ""))
    comp_src = str(row.get("المنافس", ""))
    diff = _safe_float(row.get("الفرق", 0))
    match_pct = _safe_float(row.get("نسبة_التطابق", 0))
    decision = str(row.get("القرار", ""))
    brand = str(row.get("الماركة", ""))
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


def _build_make_product(row: Any, section_type: str) -> Optional[dict[str, Any]]:
    """يبني منتج Make واحداً أو None إن غاب الاسم. #PRESERVED_LOGIC make_helper.py:179-247."""
    product_no = _extract_no(row)
    product_id = product_no or _clean_pid(
        row.get("معرف_المنتج") or row.get("product_id") or row.get("معرف المنتج")
        or row.get("sku") or row.get("SKU") or ""
    )
    # أول اسم غير فارغ: نتخطّى None/NaN/فراغ بدل التوقّف عندها (سلسلة or السابقة
    # كانت تتوقّف عند str(None)="None" فلا تجرّب المفاتيح البديلة).
    name = ""
    for _k in ("المنتج", "منتج_المنافس", "أسم المنتج", "اسم المنتج", "name"):
        _v = row.get(_k)
        if _v is not None and not (isinstance(_v, float) and pd.isna(_v)):
            _s = str(_v).strip()
            if _s and _s.lower() not in ("nan", "none"):
                name = _s
                break
    if not name:
        return None
    comp_price = _safe_float(row.get("سعر_المنافس", 0))
    our_price = _safe_float(
        row.get("السعر", 0) or row.get("سعر المنتج", 0) or row.get("price", 0) or 0
    )
    product: dict[str, Any] = {
        # NO = Primary Key سلة/زد؛ يرتدّ لـ product_id (معرف_المنتج) عند غياب «No.»
        # حتى لا يُحجب التحديث رغم وجود معرّف سلة فعلي. #PRESERVED make_helper.send_price_updates:371.
        "NO": product_no or product_id,
        "product_id": product_id,
        "name": name,
        "price": float(_section_price(section_type, our_price, comp_price)),
        "section": section_type,
    }
    _add_context(product, row)
    return product


def to_make_payload(df: pd.DataFrame | None, section_type: str = "update") -> list[dict[str, Any]]:
    """يحوّل DataFrame إلى قائمة منتجات Make. #PRESERVED_LOGIC make_helper.py:169-249."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    products: list[dict[str, Any]] = []
    # ⚡ PERFORMANCE FIX: استبدال iterrows البطيء بـ to_dict('records')
    for row in df.to_dict('records'):
        product = _build_make_product(row, section_type)
        if product is not None:
            products.append(product)
    return products


def _build_quantity_product(row: Any, quantity: int) -> Optional[dict[str, Any]]:
    """يبني عنصر تحديث كمية واحد (NO/product_id/name + quantity) أو None إن غاب الاسم.

    يُعيد استخدام هوية تحديث السعر نفسها (``_build_make_product``) لضمان نفس مفتاح سلة
    (NO/product_id)، لكنه يرسل **الكمية فقط** ولا يمسّ السعر (سيناريو Make منفصل).
    """
    base = _build_make_product(row, "quantity")
    if base is None:
        return None
    return {
        "NO": base["NO"],
        "product_id": base["product_id"],
        "name": base["name"],
        "quantity": max(int(quantity or 0), 0),
        "section": "quantity",
    }


def to_quantity_payload(df: pd.DataFrame | None, quantity: int) -> list[dict[str, Any]]:
    """يحوّل DataFrame إلى حمولة تحديث كمية (مظروف ``products``). خالص قابل للاختبار.

    يقابل سيناريو Make منفصلاً (Salla ``UpdateProduct`` للكمية فقط) — راجع المفتاح
    ``WEBHOOK_UPDATE_QUANTITY``. تصفير الكمية (0) ⇒ «نفذ لدينا».
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    items: list[dict[str, Any]] = []
    # ⚡ PERFORMANCE FIX: استبدال iterrows البطيء بـ to_dict('records')
    for row in df.to_dict('records'):
        item = _build_quantity_product(row, quantity)
        if item is not None:
            items.append(item)
    return items


def _build_new_product(row: Any) -> Optional[dict[str, Any]]:
    """يبني عنصر منتج جديد/مفقود بمفاتيح سلة العربية (مظروف ``data``) أو None.

    #PRESERVED_LOGIC: make_helper.send_missing_products:526-541 + الحقول المعتمدة في
    ``make/new_products_blueprint.json`` (webhook interface: product_id/أسم المنتج/
    سعر المنتج/رمز المنتج sku/الوزن/سعر التكلفة/السعر المخفض/الوصف). السعر = منافس−1
    (قاعدة المفقودات). المنتج الجديد قد لا يملك ``NO`` (Salla ID) بعدُ ⇒ يُحذف إن فرغ.
    """
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    # أول اسم غير فارغ: نتخطّى None/NaN/فراغ بدل التوقّف عندها.
    name = ""
    for _k in ("name", "المنتج", "منتج_المنافس", "أسم المنتج", "اسم المنتج"):
        _v = getter(_k)
        if _v is not None and not (isinstance(_v, float) and pd.isna(_v)):
            _s = str(_v).strip()
            if _s and _s.lower() not in ("nan", "none"):
                name = _s
                break
    if not name:
        return None
    comp_price = _safe_float(getter("سعر_المنافس", 0) or getter("comp_price", 0))
    if comp_price > 0:
        price = max(int(round(comp_price - 1)), 1)
    else:
        price = int(round(_safe_float(getter("price", 0) or getter("السعر", 0))))
    if price <= 0:
        return None
    product_no = _extract_no(row)
    pid = product_no or _clean_pid(
        getter("product_id", "") or getter("معرف_المنتج", "")
        or getter("معرف_المنافس", "") or getter("معرف المنتج", "")
    )
    item: dict[str, Any] = {
        "NO": product_no,                      # ← Primary Key سلة/زد (قد يفرغ للمنتج الجديد)
        "product_id": pid,
        "أسم المنتج": name,
        "سعر المنتج": price,                    # uinteger لموديول Salla CreateProduct
        "رمز المنتج sku": str(getter("sku", "") or getter("رمز المنتج sku", "")).strip(),
        "الوزن": int(_safe_float(getter("weight", 0) or getter("الوزن", 0)) or 1),
        "سعر التكلفة": int(round(_safe_float(getter("cost_price", 0) or getter("سعر التكلفة", 0)))),
        "السعر المخفض": int(round(_safe_float(getter("sale_price", 0) or getter("السعر المخفض", 0)))),
        "الوصف": str(getter("الوصف", "") or getter("description", "")).strip(),
    }
    img = str(
        getter("image_url", "") or getter("صورة_المنافس", "")
        or getter("صورة المنتج", "") or getter("رابط_الصورة", "")
        or getter("الصورة", "") or getter("صورة_منتجنا", "")
    ).strip()
    if img and img not in ("nan", "None"):
        item["صورة المنتج"] = img
    # ── التصنيف ── يُرسَل بمفتاح العقد «اسم التصنيف» الذي يربطه Make (لا «تصنيف المنتج»)
    category = str(
        getter("اسم التصنيف", "") or getter("تصنيف_المنتج", "")
        or getter("التصنيف_الرسمي", "") or getter("التصنيف", "")
        or getter("category", "") or getter("category_name", "")
        or getter("تصنيف المنتج", "")
    ).strip()
    if category and category not in ("nan", "None"):
        item["اسم التصنيف"] = category       # ← المفتاح الذي يقرأه Make (عقد الحمولة)
        item["تصنيف المنتج"] = category       # ← توافق خلفي مع عمود سلة
    # ── الماركة ──
    brand = str(
        getter("الماركة", "") or getter("الماركة_الرسمية", "")
        or getter("brand", "") or getter("brand_name", "")
    ).strip()
    if brand and brand not in ("nan", "None", "غير متوفر", "غير محدد"):
        item["الماركة"] = brand
    # ── brand_id / category_id (create-if-missing عبر Salla API عند توفّر التوكن) ──
    #   توحيد مسار زرّ الواجهة مع عقد make_helper كي لا يختلف الإرسال بين المسارين.
    try:
        from utils.make_helper import _resolve_brand_id, _resolve_category_id
        _idsrc = {
            "الماركة": brand, "brand_id": getter("brand_id", ""),
            "اسم التصنيف": category, "category_id": getter("category_id", ""),
        }
        _bid = _resolve_brand_id(_idsrc)
        _cid = _resolve_category_id(_idsrc)
        if _bid:
            item["brand_id"] = _bid
        if _cid:
            item["category_id"] = _cid
    except Exception:
        pass
    # تنظيف الحقول الفارغة (المنتج الجديد لا NO/sku له غالباً) — #PRESERVED_LOGIC make_helper.py:541.
    return {k: v for k, v in item.items() if v not in (None, "")}


def to_new_products_payload(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """يحوّل DataFrame مفقودات إلى حمولة المنتجات الجديدة (مفاتيح سلة العربية، مظروف ``data``).

    يقابل ``make/new_products_blueprint.json`` الذي يقرأ ``{{1.data}}`` ويُنشئ المنتج
    عبر ``salla:CreateProduct``. منفصل عن ``to_make_payload`` (تحديث الأسعار، مفاتيح
    إنجليزية، مظروف ``products``) لأن المظروف والمفاتيح يختلفان جذرياً بين السيناريوهين.
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    items: list[dict[str, Any]] = []
    # ⚡ PERFORMANCE FIX: استبدال iterrows البطيء بـ to_dict('records')
    for row in df.to_dict('records'):
        item = _build_new_product(row)
        if item is not None:
            items.append(item)
    return items


def confirmed_new_catalog_row(
    item: dict[str, Any], product_id: Any,
) -> Optional[dict[str, Any]]:
    """يبني صف كتالوج محلي من تأكيد Salla، لا من معرّف المصدر المرسل.

    يجب أن يأتي ``product_id`` من رد Make/Salla بعد الإنشاء. أي قيمة كانت في
    الحمولة الأصلية قد تكون معرّف منافس أو SKU مقترحاً، ولا تصلح لإثبات الملكية.
    """
    salla_id = _clean_pid(product_id)
    name = str(
        item.get("أسم المنتج") or item.get("اسم المنتج") or item.get("name") or ""
    ).strip()
    if not salla_id or not name:
        return None
    return {
        "product_id": salla_id,
        "product_name": name,
        "price": _safe_float(item.get("سعر المنتج", item.get("price", 0))),
    }


def sync_confirmed_new_product(item: dict[str, Any], product_id: Any) -> dict[str, Any]:
    """يدخل المنتج المؤكد إلى ``our_catalog`` محلياً؛ فشله لا ينفي تأكيد Salla."""
    row = confirmed_new_catalog_row(item, product_id)
    if row is None:
        return {"synced": False, "catalog_row": None, "error": "missing_catalog_identity"}
    try:
        from utils.db_manager import upsert_our_catalog

        stats = upsert_our_catalog(
            pd.DataFrame([row]), name_col="product_name",
            id_col="product_id", price_col="price",
        )
        return {"synced": True, "catalog_row": row, "stats": stats}
    except Exception as exc:  # لا نعكس تأكيد سلة بسبب تعطل كاش محلي
        return {"synced": False, "catalog_row": row, "error": str(exc)[:200]}


def to_csv(df: pd.DataFrame, path: Optional[str] = None) -> str:
    """يولّد CSV (UTF-8-SIG لدعم العربية في Excel)؛ يكتب للمسار إن مُرّر."""
    csv_text = df.to_csv(index=False)
    if path:
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(csv_text)
    return csv_text


class ExportService:
    """خدمة التصدير: Make payload + Salla + CSV/Excel + إرسال webhook."""

    def __init__(
        self,
        poster: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._poster = poster

    def make_payload(
        self, df: pd.DataFrame | None, section_type: str = "update",
    ) -> list[dict[str, Any]]:
        return to_make_payload(df, section_type)

    def quantity_payload(
        self, df: pd.DataFrame | None, quantity: int,
    ) -> list[dict[str, Any]]:
        """حمولة تحديث الكمية (NO/product_id/name + quantity)، مظروف ``products``."""
        return to_quantity_payload(df, quantity)

    def new_products_payload(
        self, df: pd.DataFrame | None,
    ) -> list[dict[str, Any]]:
        """حمولة المنتجات الجديدة/المفقودة (مفاتيح سلة العربية لمظروف ``data``)."""
        return to_new_products_payload(df)

    def post_to_make(
        self, url: str, products: list[dict[str, Any]], *, envelope: str = "products",
    ) -> dict[str, Any]:
        """يرسل الحمولة لـ Make (poster محقون للاختبار، وإلا requests).

        ``envelope`` = مفتاح المظروف الذي يقرأه Make: «products» لتحديث الأسعار،
        «data» للمنتجات الجديدة/المفقودة (BasicFeeder يقرأ ``{{1.data}}`` — راجع
        ``make/new_products_blueprint.json``). عند الفشل يُعاد ``body`` = نص رد Make
        الحقيقي (``response.text``) لإظهار سبب الرفض في الواجهة، لا رقم الحالة فقط.
        استلام Make للطلب ليس تأكيداً لإنشاء/تحديث المنتج: ``202 Accepted``
        أو ردّ 2xx فارغ يعيدان ``state=accepted`` و``confirmed=False``.
        يبقى ``success`` متوافقاً مع المتصلين القدامى ويعني نجاح النقل فقط؛
        على الواجهة التي ستخفي بطاقة أن تعتمد ``confirmed`` حصراً.
        """
        if not url:
            raise ExportError("Webhook URL غير مهيّأ")
        wtype = "new" if envelope == "data" else "update"
        # حارس التكرار — للمنتجات الجديدة (data) فقط: يُسقط الـSKU المُرسَل/الموجود مسبقاً
        if wtype == "new":
            kept: list[dict[str, Any]] = []
            for _it in products:
                try:
                    from utils.send_log import is_duplicate_new_sku, log_send
                    _sk = str(_it.get("رمز المنتج sku", "") or "").strip()
                    if _sk and is_duplicate_new_sku(_sk):
                        log_send("new", sku=_sk, product_name=str(_it.get("أسم المنتج", "")),
                                 response_excerpt="skipped_duplicate")
                        continue
                except Exception:
                    pass
                kept.append(_it)
            products = kept
            if not products:  # الكل مكرّر موجود مسبقاً — لا POST، والمنتج قائم فعلاً
                return {
                    "success": True, "confirmed": True, "state": "confirmed",
                    "status_code": 0, "body": "", "skipped_duplicate": True,
                }
        # ── السياج الظِّلّي (م2): تدوين فقط — لا يغيّر السعر ولا يمنع الإرسال ──
        try:
            from services.pricing_shadow import record_shadow
            record_shadow(products, path=f"export.{wtype}")
        except Exception:
            pass
        # ── أرضية خطأ الكشط (أ2): يحجز سعراً < 50% من وسيط السوق للمراجعة ──
        from services.send_floor_guard import split_below_floor
        products, blocked_low_price = split_below_floor(products)
        if not products:
            return {
                "success": True, "confirmed": False, "state": "blocked_low_price",
                "status_code": 0, "body": "", "blocked_low_price": blocked_low_price,
                "held_outside_band": [],
                "held_low_quality": [],
            }
        # ── سياج نطاق v1 (أ4، مرحلة 1 — قسم "سعر أعلى" فقط): يحجز خارج ±20% من الوسيط ──
        from services.send_band_guard import split_outside_band
        products, held_outside_band = split_outside_band(products)
        if not products:
            return {
                "success": True, "confirmed": False, "state": "held_outside_band",
                "status_code": 0, "body": "", "blocked_low_price": blocked_low_price,
                "held_outside_band": held_outside_band,
                "held_low_quality": [],
            }
        # ── بوّابة أهلية الإرسال (م4): ثقة المطابقة · توفّر السوق · حداثة البيانات ──
        # الحارسان أعلاه يحرسان السعر؛ هذا يحرس **جودة الدليل** الذي بُني عليه.
        from services.send_quality_guard import split_low_quality
        products, held_low_quality = split_low_quality(products)
        if not products:
            return {
                "success": True, "confirmed": False, "state": "held_low_quality",
                "status_code": 0, "body": "", "blocked_low_price": blocked_low_price,
                "held_outside_band": held_outside_band,
                "held_low_quality": held_low_quality,
            }
        from services.send_band_guard import record_and_strip_band_overrides
        products = record_and_strip_band_overrides(products)
        poster = self._poster
        if poster is None:  # pragma: no cover - شبكة حقيقية
            import requests

            poster = requests.post
        try:
            resp = poster(
                url, json={envelope: products},
                headers={"Content-Type": "application/json"}, timeout=30,
            )
        except Exception as exc:
            self._log_send_batch(wtype, products, 0, False, str(exc))
            return {
                "success": False, "confirmed": False, "state": "failed",
                "error": str(exc), "body": "", "status_code": 0,
            }
        code = getattr(resp, "status_code", None)
        try:
            body = (getattr(resp, "text", "") or "")[:1000]
        except Exception:
            body = ""
        http_ok = code in (200, 201, 202, 204)
        # نستخدم عقد Make الموحّد بدلاً من اعتبار كل 2xx نجاحاً نهائياً.
        from utils.make_helper import parse_webhook_result
        outcome = parse_webhook_result(bool(http_ok), int(code or 0), body)
        state = str(outcome["state"])
        product_id = outcome.get("product_id")
        # إنشاء منتج جديد بلا Salla ID ليس تأكيداً؛ كما أن معرّفاً واحداً لا
        # يثبت دفعة من عدة منتجات. نبقي الدفعة accepted حتى يعيد Make عقداً
        # مفصلاً لكل عنصر، فلا تختفي عناصر أو تتكرر بسبب إعادة المحاولة.
        if wtype == "new" and state == "confirmed":
            if not product_id:
                state = "accepted"
                outcome["message"] = (
                    "📨 استلم Make الطلب، لكن لم يُعِد معرّف منتج من سلة بعد"
                )
            elif len(products) != 1:
                state = "accepted"
                product_id = None
                outcome["message"] = (
                    "📨 استلم Make الدفعة، لكن يلزم تأكيد Salla مفصّل لكل منتج قبل اعتمادها"
                )
        confirmed = state == "confirmed"
        catalog_sync: dict[str, Any] = {}
        if wtype == "new" and confirmed:
            catalog_sync = sync_confirmed_new_product(products[0], product_id)
        result: dict[str, Any] = {
            "success": http_ok and state != "failed",
            "confirmed": confirmed, "state": state,
            "product_id": product_id, "message": outcome["message"],
            "status_code": code, "body": body,
            # Keep the exact accepted payload so a mixed bulk batch can mark
            # only confirmed rows as processed in the UI.
            "delivered_items": list(products) if confirmed else [],
        }
        if wtype == "new":
            result["catalog_synced"] = bool(catalog_sync.get("synced"))
            result["catalog_row"] = catalog_sync.get("catalog_row")
            if catalog_sync.get("error"):
                result["catalog_sync_error"] = catalog_sync["error"]
        if not result["success"]:
            # رسالة الخطأ الحقيقية من Make (سبب الرفض) لا رقم الحالة فقط.
            result["error"] = f"HTTP {code}: {body}" if body else f"HTTP {code}"
        if blocked_low_price:
            result["blocked_low_price"] = blocked_low_price
        if held_outside_band:
            result["held_outside_band"] = held_outside_band
        if held_low_quality:
            result["held_low_quality"] = held_low_quality
        # ``send_log.success`` دليل إرسال نهائي فقط، لا مجرد قبول HTTP.
        self._log_send_batch(wtype, products, code or 0, confirmed, body or "")
        return result

    @staticmethod
    def _log_send_batch(wtype, products, http_status, success, excerpt):
        """يدوّن دفعة إرسال في send_log (آمن: أي خطأ يُبتلع — لا يمنع الإرسال أبداً).
        صف لكل منتج في المسارين (العمود الفقري للتغذية الراجعة): الجديد (data)
        بمفاتيح سلة العربية + سعر الإرسال؛ التحديث (products) بمفاتيحه الإنجليزية
        (sku من NO/product_id + السعر المُرسَل فعلاً)."""
        try:
            from utils.send_log import log_send
            if wtype == "new":
                for _it in products:
                    log_send("new", sku=str(_it.get("رمز المنتج sku", "")),
                             product_name=str(_it.get("أسم المنتج", "")),
                             category=str(_it.get("اسم التصنيف", "")),
                             brand=str(_it.get("الماركة", "")),
                             price=_it.get("سعر المنتج"),
                             http_status=http_status, success=success, response_excerpt=excerpt)
            else:
                for _it in products:
                    log_send("update",
                             sku=str(_it.get("NO") or _it.get("product_id") or ""),
                             product_name=str(_it.get("name", "")),
                             price=_it.get("price"),
                             http_status=http_status, success=success, response_excerpt=excerpt)
        except Exception:
            pass

    def salla_dataframe(self, missing_df: pd.DataFrame, *args: Any, **kwargs: Any):
        """يلتفّ على مُولّد سلة القانوني (استيراد كسول — يستورد Streamlit)."""
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        try:
            from utils.salla_shamel_export import (  # type: ignore
                build_salla_shamel_dataframe,
            )
        except Exception as exc:
            raise ExportError("تعذّر تحميل مُصدّر سلة الشامل", error=str(exc)) from exc
        return build_salla_shamel_dataframe(missing_df, *args, **kwargs)

    @staticmethod
    def to_csv(df: pd.DataFrame, path: Optional[str] = None) -> str:
        return to_csv(df, path)

    @staticmethod
    def to_excel(df: pd.DataFrame, path: str) -> str:
        """يكتب Excel (يتطلب openpyxl)؛ يرفع ExportError إن غاب المحرّك."""
        try:
            df.to_excel(path, index=False)
        except Exception as exc:
            raise ExportError("تعذّر توليد Excel", error=str(exc)) from exc
        return path
