"""services/brand_manager.py — إدارة الماركات وتصدير ملفات ماركات سلة.

يتحقق إن كانت ماركة المنتج موجودة في متجر سلة.
إذا غير موجودة → يُنتج ملف Excel بتنسيق استيراد سلة (7 أعمدة)
ليُحمّل يدوياً قبل إرسال المنتج.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── أعمدة ملف استيراد ماركات سلة (7 أعمدة) ──────────────────────────
BRAND_COLUMNS = [
    "اسم الماركة",
    "وصف مختصر عن الماركة",
    "صورة شعار الماركة",
    "(إختياري) صورة البانر",
    "(Page Title) عنوان صفحة العلامة التجارية",
    "(SEO Page URL) رابط صفحة العلامة التجارية",
    "(Page Description) وصف صفحة العلامة التجارية",
]

# ── مصدر الماركات المعروفة = ماركات متجرنا الحقيقية من الكتالوج المرفوع ──────
# يُكتب تلقائياً عند كل تحليل/استعادة عبر ``save_store_brands`` (عمود «الماركة»).
# #FIX: كان يقارن ضدّ ملف مشروع آخر (ايدول) فيصنّف ماركات مهووس كمفقودة.
try:
    from conf.constants import DATA_DIR as _DATA_DIR
    _STORE_BRANDS_FILE = Path(_DATA_DIR) / "store_brands.csv"
except Exception:  # احتياط: مسار محلي
    _STORE_BRANDS_FILE = Path("data") / "store_brands.csv"

_BRANDS_FILE_PATHS = [
    _STORE_BRANDS_FILE,
    Path("data/brands_catalog.xlsx"),
    Path("data/brands_catalog.csv"),
]

_known_brands: set[str] | None = None


def save_store_brands(our_df: Any) -> int:
    """يحفظ ماركات متجرنا الحقيقية (عمود «الماركة» من الكتالوج) كمرجع للفحص.

    يُستدعى بعد تحميل/استعادة الكتالوج فيصبح فحص «ماركة غير موجودة» دقيقاً
    (مقارنة ضدّ ماركاتنا الفعلية لا ضدّ ملف خارجي). يعيد عدد الماركات المحفوظة.
    """
    global _known_brands
    try:
        if our_df is None or not hasattr(our_df, "columns"):
            return 0
        col = next((c for c in ("الماركة", "الماركة_الرسمية", "brand")
                    if c in our_df.columns), None)
        if col is None:
            return 0
        brands = sorted({
            str(v).strip() for v in our_df[col].dropna()
            if str(v).strip() and str(v).strip().lower() not in ("nan", "none", "غير متوفر", "غير محدد")
        })
        if not brands:
            return 0
        _STORE_BRANDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"brand": brands}).to_csv(
            _STORE_BRANDS_FILE, index=False, encoding="utf-8-sig",
        )
        _known_brands = None  # إبطال الكاش ليُعاد التحميل بالماركات الجديدة
        logger.info("📦 حُفظت %d ماركة متجر مرجعاً للفحص", len(brands))
        return len(brands)
    except Exception as exc:  # noqa: BLE001
        logger.warning("تعذّر حفظ ماركات المتجر: %s", exc)
        return 0


def _normalize_brand(name: str) -> str:
    """يوحّد اسم الماركة للمقارنة (lowercase + إزالة مسافات زائدة)."""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def load_known_brands(force: bool = False) -> set[str]:
    """يحمّل الماركات المعروفة من الملفات المتاحة (كاش في الذاكرة)."""
    global _known_brands
    if _known_brands is not None and not force:
        return _known_brands

    brands: set[str] = set()
    for path in _BRANDS_FILE_PATHS:
        try:
            if not path.exists():
                continue
            if path.suffix == ".xlsx":
                df = pd.read_excel(path, engine="openpyxl")
            else:
                df = pd.read_csv(path, encoding="utf-8-sig")
            # العمود الأول عادة هو اسم الماركة
            col = df.columns[0] if len(df.columns) > 0 else None
            if col:
                for val in df[col].dropna():
                    normalized = _normalize_brand(val)
                    if normalized and normalized not in ("nan", "none"):
                        brands.add(normalized)
            logger.info("📦 حُمّلت %d ماركة من %s", len(brands), path.name)
            break  # نجاح — لا نحتاج الملفات الأخرى
        except Exception as exc:
            logger.debug("فشل تحميل ماركات من %s: %s", path, exc)

    _known_brands = brands
    return brands


def is_brand_known(brand_name: str) -> bool:
    """يتحقق إن كانت الماركة موجودة في كتالوج سلة."""
    if not brand_name or brand_name in ("غير متوفر", "غير محدد", "nan", "None"):
        return False
    brands = load_known_brands()
    if not brands:
        return True  # لا ملف ماركات → نفترض موجودة (لا نعيق الإرسال)
    return _normalize_brand(brand_name) in brands


def extract_brand_from_name(product_name: str) -> str:
    """يستخرج الماركة من اسم المنتج (كلمة إنجليزية أولى أو بعد 'من')."""
    name = str(product_name).strip()
    if not name:
        return ""

    # بحث عن "من [ماركة]" في الاسم العربي
    match = re.search(r"من\s+(\S+)", name)
    if match:
        brand = match.group(1).strip()
        if len(brand) >= 2:
            return brand

    # أول كلمة إنجليزية في الاسم
    eng_match = re.search(r"\b([A-Za-z][A-Za-z'&.\-]+(?:\s[A-Za-z]+)?)\b", name)
    if eng_match:
        return eng_match.group(1).strip()

    return ""


def _generate_slug(brand_name: str) -> str:
    """يولّد slug من اسم الماركة (حروف إنجليزية + أرقام + شُرطة)."""
    # أزل الأقواس والرموز
    slug = re.sub(r"[^\w\s-]", "", brand_name)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug.lower()[:80]


def _generate_brand_description(brand_name: str) -> str:
    """يولّد وصفاً مختصراً للماركة."""
    return (
        f"تسوّق أفضل منتجات {brand_name} الأصلية بأسعار منافسة. "
        f"جميع منتجات {brand_name} مضمونة 100% مع شحن سريع وتغليف هدية مجاني."
    )


def _generate_seo_title(brand_name: str) -> str:
    """يولّد عنوان SEO لصفحة الماركة."""
    return f"منتجات {brand_name} الأصلية | أفضل الأسعار | مهووس للعطور"[:70]


def _generate_seo_description(brand_name: str) -> str:
    """يولّد وصف SEO لصفحة الماركة."""
    return (
        f"اكتشف تشكيلة {brand_name} الأصلية. عطور ومستحضرات عناية بجودة عالمية "
        f"بأسعار تنافسية. ✅ أصلي 100% ✅ شحن سريع ✅ ضمان الجودة"
    )[:160]


def build_brand_row(brand_name: str, logo_url: str = "") -> dict[str, str]:
    """يبني صف ماركة واحد بتنسيق استيراد سلة (7 أعمدة)."""
    slug = _generate_slug(brand_name)
    return {
        "اسم الماركة": brand_name,
        "وصف مختصر عن الماركة": _generate_brand_description(brand_name),
        "صورة شعار الماركة": logo_url if logo_url else "",
        "(إختياري) صورة البانر": "",
        "(Page Title) عنوان صفحة العلامة التجارية": _generate_seo_title(brand_name),
        "(SEO Page URL) رابط صفحة العلامة التجارية": slug,
        "(Page Description) وصف صفحة العلامة التجارية": _generate_seo_description(brand_name),
    }


def generate_brands_excel(brands: list[dict[str, str]]) -> bytes:
    """يولّد ملف Excel بتنسيق استيراد ماركات سلة. يُرجع bytes."""
    df = pd.DataFrame(brands, columns=BRAND_COLUMNS)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="ماركات")
        # تنسيق العرض
        ws = writer.sheets["ماركات"]
        for col_idx, col_name in enumerate(BRAND_COLUMNS, 1):
            ws.column_dimensions[chr(64 + col_idx)].width = max(20, len(col_name) + 5)
    buf.seek(0)
    return buf.read()


def check_and_prepare_brands(
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    """يفحص ماركات المنتجات ويُرجع (ماركات_مفقودة_كصفوف, أسماء_المفقودة).

    Args:
        products: قائمة منتجات (كل منتج dict بحقل الماركة).

    Returns:
        (brand_rows, missing_names) — صفوف Excel للماركات المفقودة + أسماؤها.
    """
    seen: set[str] = set()
    missing_rows: list[dict[str, str]] = []
    missing_names: list[str] = []

    for product in products:
        getter = product.get if hasattr(product, "get") else (lambda k, d=None: d)
        brand = str(
            getter("الماركة", "") or getter("الماركة_الرسمية", "")
            or getter("brand", "") or getter("brand_name", "")
        ).strip()

        if not brand or brand in ("غير متوفر", "غير محدد", "nan", "None"):
            # محاولة استخراج من الاسم
            name = str(
                getter("المنتج", "") or getter("منتج_المنافس", "")
                or getter("أسم المنتج", "") or getter("name", "")
            ).strip()
            brand = extract_brand_from_name(name)

        if not brand:
            continue

        normalized = _normalize_brand(brand)
        if normalized in seen:
            continue
        seen.add(normalized)

        if not is_brand_known(brand):
            # جلب لوغو من بيانات المنتج
            logo = str(
                getter("صورة_المنافس", "") or getter("image_url", "")
                or getter("صورة المنتج", "") or getter("الصورة", "")
            ).strip()
            if logo in ("nan", "None"):
                logo = ""

            missing_rows.append(build_brand_row(brand, logo_url=""))
            missing_names.append(brand)

    return missing_rows, missing_names


def add_to_known_brands(brand_names: list[str]) -> None:
    """يضيف ماركات جديدة للكاش (بعد تأكيد المستخدم أنه حمّلها)."""
    brands = load_known_brands()
    for name in brand_names:
        brands.add(_normalize_brand(name))
