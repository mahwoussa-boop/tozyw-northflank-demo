"""services/category_classifier.py — تصنيف المنتج إلى تصنيف متجر مهووس الصحيح.

يحلّ خللاً جوهرياً: النظام كان عطريّاً فيفرض «عطور رجالية» على **كل** منتج —
حتى زبدة ترطيب الجسم وعلاج الشعر! هذا المُصنِّف يكشف نوع المنتج عبر:
  • **الماركة** (إشارة قويّة: Schwarzkopf/شوارزكوف ⇒ عناية لا عطر)، ثم
  • كلمات الاسم (مكياج/عناية/شعر/جسم/بخور/شموع/منزل/إكسسوار)،
ويُرجع اسم التصنيف الورقي المطابق لشجرة متجرك (قابل لمطابقة Make التامّة).

خالص بلا AI/شبكة — حتمي وقابل للاختبار. الترتيب مهمّ: الماركة والأكثر تخصّصاً أولاً.
"""
from __future__ import annotations

import re


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def _gender(text: str) -> str:
    """نسائية | رجالية | '' (غير محدد)."""
    t = _norm(text)
    if any(k in t for k in ("نسائي", "نساء", "للنساء", "النسائي", "women", "woman",
                            "femme", "for her", "lady")):
        return "نسائية"
    if any(k in t for k in ("رجالي", "رجال", "للرجال", "الرجالي", "men", "man",
                            "homme", "for him", "pour homme")):
        return "رجالية"
    return ""


# ── ماركات عناية/شعر معروفة (إشارة قاطعة: ليست عطوراً) ──────────────────────
_CARE_BRANDS = (
    "schwarzkopf", "شوارزكوف", "بوناويل", "bonawell", "pantene", "بانتين",
    "garnier", "غارنييه", "loreal paris", "loreal", "لوريال", "tresemme",
    "تريسمي", "sunsilk", "سنسيلك", "head & shoulders", "هيد اند شولدرز",
    "gliss", "جليس", "syoss", "سيوس", "herbal essences", "هيربل", "vatika",
    "فاتيكا", "dove", "دوف", "nivea", "نيفيا", "neutrogena", "نيوتروجينا",
    "cerave", "سيرافي", "the ordinary", "اوردينري", "cetaphil", "سيتافيل",
    "eucerin", "يوسيرين", "vichy", "فيشي", "bioderma", "بيوديرما",
)
_MAKEUP_BRANDS = (
    "maybelline", "ميبلين", "mac", "هدى بيوتي", "huda", "nars", "نارس",
    "ريميل", "rimmel", "essence", "ايسنس", "wet n wild", "بورجوا", "bourjois",
    "نوت", "note",
)

# ── كلمات الأنواع ──────────────────────────────────────────────────────────
_MAKEUP = (
    "مكياج", "ميك اب", "ميكب", "احمر شفاه", "أحمر شفاه", "روج", "ليبستك",
    "ماسكرا", "مسكرا", "ريمل", "كحل", "ايلاينر", "آيلاينر", "فاونديشن",
    "كونسيلر", "بلاشر", "خدود", "هايلايتر", "برونزر", "ظلال", "اي شادو",
    "مناكير", "طلاء اظافر", "lipstick", "mascara", "eyeliner", "foundation",
    "concealer", "blush", "highlighter", "bronzer", "eyeshadow", "makeup", "nail",
    "بي بي كريم", "bb cream", "سي سي كريم", "cc cream", "كريم أساس", "كريم اساس",
)
# عناية الشعر (علاج/تغذية) — تذهب إلى «العناية»
_HAIR_CARE = (
    "شامبو", "shampoo", "بلسم", "كنديشنر", "conditioner", "كيراتين", "keratin",
    "بروتين", "protein", "حمام كريم", "حمام زيت", "زيت ساخن", "علاج الشعر",
    "مغذي الشعر", "تساقط الشعر", "زيت شعر", "سيروم شعر", "ماسك شعر",
    "treatment", "للشعر", "بوناويل",
)
# عطر/مست الشعر — تذهب إلى «عطور الشعر»
_HAIR_FRAGRANCE = ("مست شعر", "عطر شعر", "معطر شعر", "بخاخ شعر معطر", "hair mist", "hair perfume")
# عناية البشرة/الوجه
_SKIN_CARE = (
    "غسول", "مقشر", "سكراب", "scrub", "سيروم", "serum", "تونر", "toner",
    "واقي شمس", "sunscreen", "كريم وجه", "مرطب وجه", "عناية بالبشره",
    "عناية بالوجه", "عناية بالبشرة", "cleanser", "skincare", "ريتينول",
    "retinol", "نياسيناميد", "ماسك للوجه", "ماسك ورقي", "ديودرنت", "مزيل عرق",
)


def classify_category(name: str, gender_hint: str = "", brand: str = "") -> str:
    """يُرجع اسم تصنيف متجر مهووس الورقي الأنسب (قابل لمطابقة Make).

    يستخدم الماركة كإشارة قويّة (عناية/مكياج) ثم كلمات الاسم؛ وإلا عطر حسب
    النوع/الجنس. ``brand`` اختياري لكنه يرفع الدقّة كثيراً (مثل Schwarzkopf).
    """
    t = _norm(f"{name} {gender_hint}")
    b = _norm(brand)
    g = _gender(t)

    # ── 0) إشارة الماركة (قاطعة) ──
    if b and any(cb in b for cb in _CARE_BRANDS):
        # ماركة عناية/شعر معروفة ⇒ ليست عطراً
        if any(k in t for k in _MAKEUP):
            return "المكياج"
        return "العناية"
    if (b and any(mb in b for mb in _MAKEUP_BRANDS)) or any(k in t for k in _MAKEUP):
        return "المكياج"

    # ── منتجات الجسم ──
    if (any(k in t for k in ("زبده", "زبدة", "مرطب", "لوشن", "كريم", "بلسم", "ميلك"))
            and any(k in t for k in ("جسم", "بشره", "بشرة", "ترطيب", "يدين", "قدمين"))) \
            or any(k in t for k in ("body butter", "body lotion", "body cream", "moistur")):
        return "مرطبات الجسم"
    if any(k in t for k in ("بودره", "بودرة", "بدرة", "powder")) and "جسم" in t:
        return "بودرة الجسم"

    # ── الشعر: عناية (علاج/تغذية) ⇒ العناية ؛ عطر/مست ⇒ عطور الشعر ──
    _hair_treatment = (
        any(k in t for k in _HAIR_CARE)
        or (any(w in t for w in ("علاج", "حمام")) and any(k in t for k in ("زيت", "شعر", "كريم", "بروتين", "كيراتين")))
    )
    if any(k in t for k in _HAIR_FRAGRANCE):
        return "عطور الشعر"
    if _hair_treatment:
        return "العناية"

    # ── عناية البشرة/الوجه ──
    if any(k in t for k in _SKIN_CARE):
        return "العناية"

    # ── البخور والعود ── («بخور فاخر» غير موجود؛ الأمّ «العود والبخور» تطابق كل بخور
    #    وعود — تصنيف متجر فعليّ، بدل اسم لا يُطابَق فيُنتِج categories=[] ⇒ 422)
    if any(k in t for k in ("بخور", "دخون", "معمول")):
        return "العود و البخور"
    if "عود" in t and any(k in t for k in ("طبيعي", "كمبودي", "هندي", "تقطيع", "ماليزي")):
        return "عود طبيعي"
    # ── شموع معطرة ──
    if any(k in t for k in ("شمعه", "شمعة", "شموع", "candle")):
        return "شموع معطرة"
    # ── معطرات المنزل ──
    if any(k in t for k in ("معطر جو", "معطر هواء", "معطر مفارش", "معطر اراضي",
                            "معطر منزل", "معطر المنزل", "room spray", "air fresh")):
        return "معطرات هواء"
    # ── عطور الجسم (بادي مست/سبلاش) ──
    if any(k in t for k in ("بادي مست", "بادي سبلاش", "بادي سبراي", "سبلاش",
                            "body mist", "body splash", "body spray")):
        return "عطور الجسم"
    # ── إكسسوارات ──
    if any(k in t for k in ("اكسسوار", "إكسسوار", "accessor")):
        return "إكسسوارات العطور"

    # ══ العطور (افتراضي بعد استبعاد غير العطري) ══
    if any(k in t for k in ("اطفال", "أطفال", "طفل", "بيبي", "baby", "kids", "children")):
        return "عطور الأطفال"
    if any(k in t for k in ("تستر", "تيستر", "tester")):
        # تسمية المتجر غير متماثلة: «عطور التستر نسائية» مقابل «عطور التستر رجالية»
        if g == "نسائية":
            return "عطور التستر نسائية"
        if g == "رجالية":
            return "عطور التستر رجالية"
        return "عطور التستر جديدة"
    if any(k in t for k in ("نيش", "niche")):
        return f"عطور النيش {g}" if g else "عطور النيش للجنسين"
    if any(k in t for k in ("فرمون", "فيرمون", "pheromone")):
        return f"عطور فرمونية {g}" if g else "عطور فرمونية للجنسين"
    if any(k in t for k in ("بديل", "بدائل", "تقليد", "dupe", "impression")):
        return f"بدائل العطور {g}" if g else "بدائل العطور جديدة"
    # سياسة المالك: جنس معروف ⇒ مُجنَّس صحيح؛ مجهول ⇒ «العطور» العامّ (لا فرض «رجالية»
    #   على الجميع). معظم أسماء العطور بلا كلمة جنس ⇒ هذا يمنع تصنيف الكلّ رجالياً خطأً.
    return f"عطور {g}" if g else "العطور"
