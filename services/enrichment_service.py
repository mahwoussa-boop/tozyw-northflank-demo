"""services/enrichment_service.py — بوابة إثراء المنتجات المفقودة (القاعدة الذهبية).

يدمج منطق التنسيق الذهبي من الكود القديم (mahwous-scraper-32):
- ``is_enriched``: يتحقق أن المنتج مرّ بخط الإثراء (وصف + صورة + ماركة).
- ``enrich_row``: يُثري صف مفقود ببيانات من الكشط والتنسيق.
- ``validate_for_make``: يتحقق أن المنتج جاهز للإرسال إلى Make.

القاعدة الذهبية: لا يُسمح بإرسال منتج مفقود إلى Make إلا بعد:
1. وجود وصف بتنسيق مهووس (يحتوي علامات مهووس المعروفة).
2. وجود صورة حقيقية (ليست placeholder).
3. وجود ماركة مطابقة.

#PRESERVED_LOGIC: product_gate.py:is_mahwous_description + is_real_image_url (الكود القديم).
"""
from __future__ import annotations

import re
from typing import Any

# علامات تنسيق مهووس — يجب أن يحتوي الوصف على واحدة منها على الأقل.
# #PRESERVED_LOGIC: product_gate.py:_MAHWOUS_MARKERS (الكود القديم).
_MAHWOUS_MARKERS: tuple[str, ...] = (
    "مهووس", "الهرم العطري", "المكونات العطرية", "لمسة خبير",
    "لماذا تختار", "رحلة العطر", "تفاصيل المنتج", "النفحات",
    "مقدمة العطر", "قلب العطر", "قاعدة العطر", "مميزات المنتج",
    "العائلة العطرية", "شخصية العطر",
    # ── علامات العناية والمكياج ──
    "المكونات الفعّالة", "همسة من خبير", "همسة من خبير مهووس",
    "طريقة الاستخدام", "كلمات البحث", "ضمان الأصالة",
)


_MIN_DESC_LENGTH = 60  # حدّ أدنى لطول الوصف الفعلي (بدون HTML).
_PLACEHOLDER_PATTERNS = re.compile(
    r"placeholder|no.?image|default\.(png|jpg)|1x1|blank\.(gif|png)|data:",
    re.IGNORECASE,
)


def is_mahwous_description(desc: Any) -> bool:
    """يتحقق أن الوصف بتنسيق مهووس (يحتوي علامة معروفة + طول كافٍ). خالص.

    #PRESERVED_LOGIC: product_gate.py:is_mahwous_description.
    """
    if not desc:
        return False
    text = str(desc)
    # إزالة HTML للحساب الفعلي
    plain = re.sub(r"<[^>]+>", "", text).strip()
    if len(plain) < _MIN_DESC_LENGTH:
        return False
    return any(marker in text for marker in _MAHWOUS_MARKERS)


def is_real_image_url(url: Any) -> bool:
    """يتحقق أن الصورة رابط حقيقي (ليس placeholder أو data:). خالص.

    #PRESERVED_LOGIC: product_gate.py:is_real_image_url.
    """
    if not url:
        return False
    s = str(url).strip()
    if not s.lower().startswith(("http://", "https://", "//")):
        return False
    return not _PLACEHOLDER_PATTERNS.search(s)


def is_enriched(row: Any) -> bool:
    """يتحقق أن صف المنتج المفقود مرّ بخط الإثراء الكامل. خالص.

    المتطلبات:
    1. وصف بتنسيق مهووس (``is_mahwous_description``).
    2. صورة حقيقية (``is_real_image_url``).
    3. ماركة موجودة (غير فارغة).
    """
    if row is None:
        return False
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)

    # علم صريح (يُعيّنه خط الإثراء عند نجاحه)
    if getter("_enriched", False):
        return True

    # فحص ضمني: هل تتوفر البيانات الذهبية؟
    desc = getter("الوصف") or getter("وصف_AI") or getter("description") or ""
    image = getter("صورة_المنافس") or getter("صورة المنتج") or getter("image_url") or ""
    brand = getter("الماركة") or getter("الماركة_الرسمية") or getter("brand") or ""

    return (
        is_mahwous_description(desc)
        and is_real_image_url(image)
        and bool(str(brand).strip())
    )


def validate_for_make(row: Any) -> tuple[bool, str]:
    """يتحقق من جاهزية المنتج المفقود للإرسال إلى Make. يعيد (OK, رسالة).

    شروط:
    1. ``is_enriched`` — الإثراء الكامل.
    2. سعر > 0.
    """
    if not is_enriched(row):
        return False, "⚠️ يتطلب الكشط والتنسيق الذهبي أولاً (وصف + صورة + ماركة)"
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    try:
        price = float(getter("سعر_المنافس", 0) or getter("price", 0) or 0)
    except (ValueError, TypeError):
        price = 0.0
    if price <= 0:
        return False, "⚠️ سعر المنتج مفقود أو صفر"
    return True, "✅ جاهز للإرسال"


# ── أسباب رفض «بوابة الجودة» (مصدر واحد للحقيقة — يُستهلَك في التقرير/الواجهة) ──
GATE_REASON_PRICE = "سعر ≤ 0"
GATE_REASON_IMAGE = "لا صورة حقيقية"
GATE_REASON_DESC = "وصف قصير أو بلا علامة مهووس"
GATE_REASON_BRAND = "لا ماركة"


def gate_reason(row: Any) -> str:
    """يُعيد سبب رفض «بوابة الجودة» لصف منتج مفقود، أو "" إذا عبر. خالص (لا يعدّل الصف).

    بيان فقط — يطابق منطق ``missing_orchestrator._apply_quality_gate`` تماماً ولا
    يغيّر القبول/الرفض (#PRESERVED_LOGIC). يُعيد أوّل شرط فاشل بالترتيب:
      1. ``GATE_REASON_PRICE``  — سعر ≤ 0
      2. ``GATE_REASON_IMAGE``  — لا صورة حقيقية
      3. ``GATE_REASON_DESC``   — وصف قصير أو بلا علامة مهووس
      4. ``GATE_REASON_BRAND``  — لا ماركة
    """
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)

    # 1) السعر — نفس مفاتيح ودلالة _safe_float في البوابة (فارغ/nan ⇒ 0).
    price_raw = (
        getter("سعر_المنافس")
        or getter("السعر_المقترح")
        or getter("price")
        or getter("comp_price")
        or 0
    )
    try:
        price = (
            0.0
            if str(price_raw).strip() in ("", "nan", "None", "NaN")
            else float(price_raw)
        )
    except (ValueError, TypeError):
        price = 0.0
    if price <= 0:
        return GATE_REASON_PRICE

    # عبر الإثراء الكامل (يحترم علم _enriched والثلاثي الذهبي) ⇒ لا سبب رفض.
    if is_enriched(row):
        return ""

    # 2-4) أيّ ركن من الإثراء سقط — نفس مفاتيح is_enriched، بترتيب صورة←وصف←ماركة.
    desc = getter("الوصف") or getter("وصف_AI") or getter("description") or ""
    image = getter("صورة_المنافس") or getter("صورة المنتج") or getter("image_url") or ""
    brand = getter("الماركة") or getter("الماركة_الرسمية") or getter("brand") or ""
    if not is_real_image_url(image):
        return GATE_REASON_IMAGE
    if not is_mahwous_description(desc):
        return GATE_REASON_DESC
    return GATE_REASON_BRAND

