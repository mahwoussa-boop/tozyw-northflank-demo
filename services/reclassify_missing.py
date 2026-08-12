# -*- coding: utf-8 -*-
"""services/reclassify_missing.py — اقتراح إعادة تصنيف صفوف المفقودات (نقي، غير موصول).

⚠️ غير موصول بأي مسار حي — يُستهلك فقط من سكربت البروفة الجافة والاختبارات.
القواعد مربوطة حرفياً بنصوص ``حالة_المراجعة`` الفعلية في missing_df الحي:
«متوفّر بحجم مختلف» · «جنس مختلف — تأكيد بشري» · «بانتظار التحقق».
لا يحذف صفاً ولا يغيّر green — يقترح (level, move_reason, priority) فقط.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# النصوص الحرفية من matching_service._decide (مثبّتة من بيانات حية 2026-07-04).
REASON_SIZE = "متوفّر بحجم مختلف"
REASON_GENDER = "جنس مختلف — تأكيد بشري"

_BRAND_BLANKS = {"", "nan", "none", "null", "غير محدد", "غير متوفر", "-"}
_NAME_COLS = ("المنتج", "أسم المنتج", "اسم المنتج", "product_name")
_BRAND_COLS = ("الماركة", "الماركة_الرسمية", "brand", "brand_name")


def _s(row: Mapping[str, Any], key: str) -> str:
    v = row.get(key) if hasattr(row, "get") else None
    s = str(v).strip() if v is not None else ""
    return "" if s.lower() in ("nan", "none", "<na>") else s


def _score(row: Mapping[str, Any]) -> float:
    try:
        return float(row.get("درجة_التشابه") or 0)
    except (TypeError, ValueError):
        return 0.0


def _brand_tokens(brand: str) -> frozenset[str]:
    """رموز الماركة للمقارنة المتحفّظة (قيم نائبة ⇒ فارغ). نقية."""
    b = str(brand or "").strip()
    if b.casefold() in _BRAND_BLANKS:
        return frozenset()
    toks = {t for t in b.casefold().replace("-", " ").replace("_", " ").split() if t}
    return frozenset(toks - {"ال"})


def brands_differ(brand_a: str, brand_b: str) -> bool:
    """مختلفتان فقط إذا كانت كلتاهما موجودتين ولا تشتركان بأي رمز.
    متحفّظ: «نيكولاي NICOLAI» و«نيكولاي» تشتركان ⇒ ليستا مختلفتين."""
    ta, tb = _brand_tokens(brand_a), _brand_tokens(brand_b)
    return bool(ta) and bool(tb) and not (ta & tb)


def build_our_brand_index(our_catalog_df: Any) -> dict[str, str]:
    """فهرس {اسم منتجنا الدقيق ← ماركته} من كتالوجنا (قراءة فقط، آمن من الغياب)."""
    if our_catalog_df is None or getattr(our_catalog_df, "empty", True):
        return {}
    cols = set(map(str, our_catalog_df.columns))
    name_col = next((c for c in _NAME_COLS if c in cols), None)
    brand_col = next((c for c in _BRAND_COLS if c in cols), None)
    if not name_col or not brand_col:
        return {}
    out: dict[str, str] = {}
    for name, brand in zip(our_catalog_df[name_col], our_catalog_df[brand_col]):
        n, b = str(name or "").strip(), str(brand or "").strip()
        if n and b.casefold() not in _BRAND_BLANKS:
            out.setdefault(n, b)
    return out


def propose(
    row: Mapping[str, Any],
    our_brands: Optional[Mapping[str, str]] = None,
) -> tuple[str, str, float]:
    """يقترح (level, move_reason, priority) لصف مفقودات واحد. نقية — لا حذف.

    green ⇒ يمر دون تغيير. R1/R2/R3 ⇒ ترقية مقترحة إلى green مع سبب.
    R4 ⇒ يبقى review بأولوية = عدد_المنافسين (للترتيب فقط).
    """
    if _s(row, "مستوى_الثقة") == "green":
        return ("green", "", 0.0)

    score = _score(row)
    reason = _s(row, "حالة_المراجعة")

    if score >= 82 and reason == REASON_SIZE:      # R1
        return ("green", f"نسخة حجم غير متوفرة لدينا — مطابقة {score:.0f}%", score)

    if score >= 82 and reason == REASON_GENDER:    # R2
        return ("green", "نسخة جنس مختلفة", score)

    if 65 <= score < 82:                           # R3
        match_name = _s(row, "منتج_مطابق_محتمل")
        match_brand = (our_brands or {}).get(match_name, "")
        if brands_differ(_s(row, "الماركة"), match_brand):
            return ("green", "تشابه اسمي عابر للماركات", score)

    try:                                           # R4
        n_comp = float(row.get("عدد_المنافسين") or 0)
    except (TypeError, ValueError):
        n_comp = 0.0
    return ("review", "", n_comp)


def bucket_of(row: Mapping[str, Any], result: tuple[str, str, float]) -> str:
    """اسم السلة للتقارير: green-passthrough / R1 / R2 / R3 / R4. نقية."""
    level, reason, _ = result
    if _s(row, "مستوى_الثقة") == "green":
        return "green-passthrough"
    if level == "review":
        return "R4"
    if reason.startswith("نسخة حجم"):
        return "R1"
    if reason == "نسخة جنس مختلفة":
        return "R2"
    return "R3"
