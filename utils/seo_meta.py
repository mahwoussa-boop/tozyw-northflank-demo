"""utils/seo_meta.py — توليد بيانات SEO للمنتج (عنوان/وصف) لحمولة سلة.

نمط مطابق للوصف الذهبي: **يستخدم حقول الذكاء الصناعي إن وُجدت** (``seo_title`` /
``seo_description`` من ``ai_engine.enhance_competitor_product_for_salla``)، وإلا
يولّد بديلاً حتمياً دائماً صالحاً ضمن حدود سلة:
  • ``metadata_title``       ≤ 60 حرفاً  → مفتاح الحمولة «عنوان السيو»
  • ``metadata_description`` ≤ 160 حرفاً → مفتاح الحمولة «وصف السيو»

خالص بلا أي نداء شبكة/AI — قابل للاختبار، ويضمن امتلاء الحقلين دائماً.
"""
from __future__ import annotations

from typing import Dict, Optional

TITLE_MAX = 60
DESC_MAX = 160

_STORE = "مهووس"

# مفاتيح القراءة (عربي/إنجليزي/مخرجات الكاشط).
_NAME_KEYS = ("name", "أسم المنتج", "اسم المنتج", "المنتج", "منتج_المنافس",
              "cleaned_title", "product_name", "title")
_BRAND_KEYS = ("الماركة", "brand", "brand_name", "الماركة_الرسمية")
_FAMILY_KEYS = ("العائلة_العطرية", "fragrance_family", "family")


def _val(d: Dict, keys) -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip().lower() not in ("", "nan", "none", "غير متوفر"):
            return str(v).strip()
    return ""


def _clamp(text: str, limit: int) -> str:
    """يقصّ النص إلى ``limit`` عند حدّ كلمة (دون قطع كلمة) مع تنظيف الذيل."""
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" -|،,.").strip()


def _brand_in_name(name: str, brand: str) -> bool:
    """هل الماركة مذكورة أصلاً في الاسم؟ (تطبيع خفيف لتفادي التكرار)."""
    if not brand:
        return True
    n, b = name.lower(), brand.lower()
    return b in n or any(part in n for part in b.split() if len(part) >= 3)


def build_seo_title(name: str, brand: str = "") -> str:
    """عنوان SEO ≤ 60 حرفاً: اسم المنتج (+ الماركة إن غابت) + «| مهووس» إن اتّسع."""
    name = " ".join(str(name or "").split())
    if not name:
        name = brand or _STORE
    base = name if _brand_in_name(name, brand) else f"{name} {brand}".strip()
    base = _clamp(base, TITLE_MAX)
    suffix = f" | {_STORE}"
    if len(base) + len(suffix) <= TITLE_MAX:
        base += suffix
    return base


def build_seo_description(name: str, brand: str = "", family: str = "") -> str:
    """وصف SEO ≤ 160 حرفاً: تشجيع نقر + أصالة + فئة + شحن سريع."""
    name = " ".join(str(name or "").split()) or "المنتج"
    brand_part = f" من {brand}" if brand and not _brand_in_name(name, brand) else ""
    family_part = f" — {family}" if family else ""
    tail = " ضمان الأصالة وشحن سريع داخل السعودية."
    head = f"اشترِ {name} الأصلي{brand_part} في متجر {_STORE}{family_part}."
    # إن تجاوز الإجمالي الحدّ، قلّص الرأس واحتفظ بالذيل إن أمكن.
    if len(head) + len(tail) <= DESC_MAX:
        return head + tail
    if len(head) <= DESC_MAX:
        room = DESC_MAX - len(head)
        return head + (tail if len(tail) <= room else "")
    return _clamp(head, DESC_MAX)


import re as _re

# تحويل عربي→لاتيني للتدرّج الحتمي (حين تتعذّر ترجمة AI).
_AR2LAT = {
    "ا": "a", "أ": "a", "إ": "a", "آ": "a", "ب": "b", "ت": "t", "ث": "th",
    "ج": "j", "ح": "h", "خ": "kh", "د": "d", "ذ": "th", "ر": "r", "ز": "z",
    "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a",
    "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "و": "w", "ي": "y", "ة": "a", "ى": "a", "ؤ": "o", "ئ": "e", "ء": "",
}
_SLUG_STOP = {"من", "مل", "ml", "the", "of", "fl", "oz"}


def _translit(s: str) -> str:
    return "".join(_AR2LAT.get(ch, ch) for ch in str(s or ""))


def _size_token(text: str) -> str:
    m = _re.search(r"(\d{1,4})\s*(?:مل|ml|mL)\b", str(text or ""), _re.I)
    if m:
        return f"{m.group(1)}ml"
    m = _re.search(r"(\d{1,4})\s*(?:جم|g|gram|جرام|غرام)\b", str(text or ""), _re.I)
    return f"{m.group(1)}g" if m else ""


def _slug_join(*parts: str) -> str:
    """يدمج أجزاءً إلى slug: ASCII + أرقام + شرطة سفلية، Capitalized، بلا تكرار."""
    seen: set = set()
    out = []
    for p in parts:
        for tok in _re.split(r"[^A-Za-z0-9]+", _translit(p)):
            tl = tok.lower()
            if not tok or tl in _SLUG_STOP or tl in seen:
                continue
            seen.add(tl)
            out.append(tok if tok[:1].isupper() else tok.capitalize())
    return "_".join(out)


def build_seo_slug(name: str, brand: str = "", size: str = "", _ai=None) -> str:
    """slug إنجليزي لرابط صفحة المنتج: ``Brand_Name_Size`` (شرطة سفلية، ASCII).

    مثال: ``Schwarzkopf_Bonawell_Hot_Oil_Treatment_Wheat_225ml``. يحاول ترجمة AI
    نظيفة أولاً، ويتدرّج إلى حرفنة حتمية (بلا شبكة) فلا يكون فارغاً.
    """
    name = str(name or "").strip()
    if not name:
        return ""
    sz = _size_token(f"{size} {name}")
    # 1) ترجمة AI نظيفة (اختياري — يتدرّج بأمان)
    try:
        ai = _ai if _ai is not None else _ai_slug
        s = ai(name, brand)
        if s:
            s = _re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
            if sz and sz.lower() not in s.lower():
                s = f"{s}_{sz}"
            if len(s) >= 3:
                return s
    except Exception:
        pass
    # 2) تدرّج حتمي: حرفنة الماركة + الاسم + الحجم
    slug = _slug_join(brand, name, sz)
    if slug:
        return slug
    # 3) ضمان عدم الفراغ: في عقد Make، «رابط السيو» حاملٌ لاسم الإنشاء (إنجليزي،
    #    _→مسافة) قبل إعادة التسمية للعربي — فلو فرغ يفشل إنشاء المنتج. نتدرّج:
    #    حرفنة خام بلا تصفية كلمات وقف، ثم بصمة قصيرة من الاسم كملاذ أخير.
    raw = _re.sub(r"[^A-Za-z0-9]+", "_", _translit(f"{brand} {name}")).strip("_")
    if len(raw) >= 2:
        return raw
    import hashlib
    return "Product_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def _ai_slug(name: str, brand: str) -> str:
    """ترجمة الاسم إلى slug إنجليزي عبر AI (call_ai). '' عند التعذّر."""
    try:
        from engines.ai_engine import call_ai
    except Exception:
        return ""
    prompt = (
        "حوّل المنتج إلى slug إنجليزي لرابط صفحة المنتج بصيغة Brand_Words_Size: "
        "كلمات إنجليزية مترجمة (لا حرفنة)، ASCII فقط، مفصولة بشرطة سفلية _، "
        "بلا عربي وبلا رموز.\n"
        f"الماركة: {brand}\nالاسم: {name}\nأجب بالـslug فقط بلا شرح."
    )
    try:
        r = call_ai(prompt)
        txt = r.get("response") if isinstance(r, dict) else r
        return str(txt or "").strip()
    except Exception:
        return ""


def seo_fields(
    product: Dict,
    *,
    name: Optional[str] = None,
    brand: Optional[str] = None,
) -> Dict[str, str]:
    """يبني مفاتيح الحمولة «عنوان السيو»/«وصف السيو».

    يُفضّل ``seo_title``/``seo_description`` المُولَّدين بالـAI إن كانا حاضرين
    (مع قصّهما للحدود)، وإلا يولّد بديلاً حتمياً. ``name``/``brand`` يتجاوزان
    الاستنتاج من القاموس (لتطابق الماركة المُرسَلة في الحمولة).
    """
    nm = name if name is not None else _val(product, _NAME_KEYS)
    br = brand if brand is not None else _val(product, _BRAND_KEYS)
    fam = _val(product, _FAMILY_KEYS)

    ai_title = str(product.get("seo_title", "") or "").strip()
    ai_desc = str(product.get("seo_description", "") or "").strip()

    title = _clamp(ai_title, TITLE_MAX) if ai_title else build_seo_title(nm, br)
    desc = _clamp(ai_desc, DESC_MAX) if ai_desc else build_seo_description(nm, br, fam)
    return {"عنوان السيو": title, "وصف السيو": desc}
