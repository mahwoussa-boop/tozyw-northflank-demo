# -*- coding: utf-8 -*-
"""
engines/golden_template.py — قالب مهووس الذهبي (Golden Template)
════════════════════════════════════════════════════════════════
الوصف الاحترافي الموحَّد الذي يُرفَق بكل منتج يُرسَل إلى سلة عبر Make.

مَنقول حرفياً من قالب المستخدم الذهبي (Downloads/build_golden_template.py،
نفس BATCH_01) مع تكييف المدخلات لتُقرأ من dict المنتج في المشروع بدل ملفات
Excel/JSON. حتمي بالكامل (بلا أي استدعاء API).

البنية (مطابقة للصورة): العنوان → الافتتاحية → تفاصيل المنتج → رحلة العطر
(الهرم) → همسة خبير → ضمان الأصالة → بطاقة العطر الموثّقة → لماذا تختار →
روابط → الأسئلة الشائعة → خاتمة + كلمات البحث.

النوتات (top/heart/base) والعائلة والاسم الإنجليزي وسنة الإصدار تُقرأ من حقول
المنتج المُثراة (Fragrantica/AI). عند غيابها يتدرّج القالب تلقائياً للنسخة
العامة (بلا هرم/بطاقة) — تماماً كالأصل (has_notes gating).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

__all__ = ["build_golden_description"]


# ── استخراج الحقول من اسم المنتج (مطابق للأصل) ─────────────────────────────
def _extract_brand(product_name: str, brand_col: Any) -> str:
    if brand_col and str(brand_col).strip() and str(brand_col).strip().lower() not in ("nan", "none", "غير متوفر", "غير محدد"):
        return str(brand_col).split("|")[0].strip()
    name = product_name or ""
    if "جيرلان" in name: return "جيرلان"
    if "فرزاتشي" in name or "فيرساتشي" in name: return "فرزاتشي"
    if "ميمو" in name: return "ميمو باريس"
    if "قوتشي" in name or "غوتشي" in name: return "قوتشي"
    if "أزارو" in name: return "أزارو"
    if "كارتييه" in name: return "كارتييه"
    if "فالنتينو" in name: return "فالنتينو"
    if "لطافة" in name: return "لطافة"
    return ""  # ماركة مجهولة ⇒ يُسقَط سطرها بدل تلفيق «ماركة فاخرة»


def _extract_gender(product_name: str) -> str:
    name = product_name or ""
    if "للنساء" in name or "نسائي" in name or "للمرأة" in name or "فام" in name or "النسائي" in name:
        return "نسائي"
    elif "للرجال" in name or "رجالي" in name or "للجنسين" in name or "يونيسكس" in name:
        if "للجنسين" in name or "يونيسكس" in name:
            return "للجنسين"
        return "رجالي"
    return "للجنسين"


def _extract_concentration(product_name: str) -> str:
    name = (product_name or "").lower()
    pn = product_name or ""
    if "بارفيوم" in name or "parfum" in name.replace("eau de ", ""):
        if "أو دو بارفيوم" in pn or "او دو بارفيوم" in pn or "أو دو برفيوم" in pn or "او دي بارفيوم" in pn:
            return "أو دو بارفيوم (EDP)"
        if "edp" in name:
            return "أو دو بارفيوم (EDP)"
        return "بارفيوم (Parfum)"
    if "تواليت" in name or "edt" in name:
        return "أو دو تواليت (EDT)"
    if "كولون" in name or "cologne" in name:
        return "أو دو كولون (EDC)"
    if "انتنس" in name or "intense" in name:
        return "أو دو بارفيوم انتنس (EDP Intense)"
    return ""  # تركيز مجهول ⇒ يُسقَط سطره بدل تلفيق EDP


# وحدات غير سائلة ترد في أسماء منتجاتنا (بودرة جسم، كريم، بخور، عود).
# كتابة «100 مل» فوقها خطأ مضاعف: الحجم معلوم، والمنتج ليس سائلاً أصلاً.
_SIZE_UNITS = (
    (r"(\d+(?:\.\d+)?)\s*(?:جم|جرام|غرام)\b", "جم"),
    (r"(\d+(?:\.\d+)?)\s*(?:g|gm|gr)\b", "جم"),
    (r"(\d+(?:\.\d+)?)\s*(?:oz|أونصة)\b", "أونصة"),
)


def _extract_size(product_name: str) -> str:
    """الحجم من اسم المنتج. المجهول يبقى "" فيُسقَط سطره بدل تلفيق «100 مل»."""
    name = product_name or ""
    m = re.search(r"(\d+)\s*مل", name)
    if m: return m.group(1) + " مل"
    m = re.search(r"(\d+)\s*ml", name, re.IGNORECASE)
    if m: return m.group(1) + " مل"
    for pattern, unit in _SIZE_UNITS:
        m = re.search(pattern, name, re.IGNORECASE)
        if m: return f"{m.group(1)} {unit}"
    return ""


def _get_occasion(family: str, gender: str) -> str:
    f = (family or "").lower()
    if "شرقي" in f or "عنبر" in f or "عود" in f or "جلد" in f:
        return "المناسبات الخاصة والسهرات الراقية"
    if "زهري" in f and "فواك" in f:
        return "الخروجات اليومية والمناسبات"
    if "أروماتيك" in f or "فوجير" in f or "مائي" in f:
        return "الاستخدام اليومي والعمل"
    if "حمضي" in f or "سيتروس" in f:
        return "النهار والأجواء المنعشة"
    if "زهري" in f:
        return "اللقاءات الأنيقة والمناسبات"
    if "خشبي" in f:
        return "المساء والمناسبات الرسمية"
    return "مختلف المناسبات"


def _get_season(family: str) -> str:
    f = (family or "").lower()
    if "شرقي" in f or "عنبر" in f or "جلد" in f or "حار" in f:
        return "الشتاء والخريف"
    if "مائي" in f or "حمضي" in f or "أخضر" in f:
        return "الصيف والربيع"
    if "زهري" in f:
        return "الربيع والصيف"
    return "جميع الفصول"


# ── واصِفات النوتات الحسّية (مطابق للأصل) ──────────────────────────────────
_NOTE_DESCS = {
    "الزعفران": "دفء الزعفران الفاخر", "زعفران": "دفء الزعفران الفاخر",
    "الورد": "رقة الورد الدمشقي", "ورد": "نعومة بتلات الورد",
    "العود": "فخامة العود الأصيل", "عود": "عمق العود النادر",
    "الياسمين": "عبير الياسمين الساحر", "ياسمين": "نفحات الياسمين المخملية",
    "البرغموت": "إشراقة البرغموت المنعشة", "برغموت": "انتعاش البرغموت",
    "الفانيليا": "حلاوة الفانيليا الدافئة", "فانيليا": "دفء الفانيليا الكريمية",
    "خشب الصندل": "نعومة خشب الصندل", "خشب الأرز": "ثبات خشب الأرز",
    "المسك": "نقاء المسك الأبيض", "مسك": "لمسة المسك الناعمة",
    "الباتشولي": "عمق الباتشولي الأرضي", "باتشولي": "غنى الباتشولي",
    "العنبر": "دفء العنبر الذهبي", "عنبر": "سحر العنبر",
    "البخور": "روحانية البخور", "بخور": "عبق البخور",
    "اللبان": "صفاء اللبان", "الجلود": "أناقة الجلود", "جلد": "قوة الجلد",
    "الليمون": "حيوية الليمون", "ليمون": "نضارة الليمون",
    "النيرولي": "رقة النيرولي", "الخوخ": "عذوبة الخوخ",
    "السوسن": "أناقة السوسن البودري", "القرفة": "حرارة القرفة",
    "اللافندر": "هدوء اللافندر", "نجيل الهند": "عمق نجيل الهند",
    "الفاوانيا": "رقة الفاوانيا",
}


def _describe_note(note_name: str) -> str:
    return _NOTE_DESCS.get(note_name, note_name)


def _build_opening(family: str, top_notes: List[str], product_name: str) -> str:
    f = (family or "").lower()
    seed = hashlib.md5((product_name or "").encode()).hexdigest()
    idx = int(seed[:2], 16) % 5
    top_str = " و".join(top_notes[:2]) if top_notes else "مكونات فاخرة"
    templates = {
        "شرقي": [
            f"عطر شرقي آسر يفتتح بـ{top_str}، ليأخذك في رحلة عطرية تنبض بالدفء والغموض.",
            f"تركيبة شرقية ساحرة تبدأ بلمسات {top_str}، وتتكشف عن طبقات عميقة تسكن الذاكرة.",
            f"من قلب الشرق، عطر يجمع بين {top_str} في مقدمة آسرة تعد بتجربة استثنائية.",
            f"إطلالة شرقية فاخرة تنطلق من {top_str}، لتحكي قصة من الأصالة والسحر.",
            f"عبق شرقي أخّاذ يستهل رحلته بـ{top_str}، ويغلفك بهالة من الجاذبية والرقي.",
        ],
        "زهري": [
            f"باقة زهرية رقيقة تنفتح بـ{top_str}، كحديقة ربيعية في أوج إزهارها.",
            f"عطر زهري أنيق يبدأ بنفحات {top_str}، ليرسم صورة من النعومة والجمال.",
            f"أزهار نابضة بالحياة تتفتح مع {top_str}، في تجربة عطرية مفعمة بالأنوثة.",
            f"لوحة زهرية فنية تبدأ بـ{top_str}، وتتدرج نحو عمق آسر ينبض بالحياة.",
            f"نسمات زهرية ناعمة تنطلق من {top_str}، لتمنحك إطلالة مشرقة لا تُنسى.",
        ],
        "خشبي": [
            f"تركيبة خشبية نبيلة تفتتح بـ{top_str}، وتمتد نحو أعماق الغابات الثمينة.",
            f"عطر خشبي فاخر يبدأ رحلته بـ{top_str}، ليكشف عن شخصية قوية وثابتة.",
            f"من أعماق الأخشاب النادرة، عطر يجمع {top_str} في مقدمة جريئة.",
            f"أناقة خشبية عصرية تنطلق من {top_str}، لعطر يليق بصاحب الذوق الرفيع.",
            f"حضور خشبي مهيب يستهل بـ{top_str}، ويتطور نحو تركيبة عميقة لا تُقاوم.",
        ],
        "حمضي": [
            f"انفجار منعش من {top_str}، يضفي حيوية فورية تشعل الحواس.",
            f"انتعاش حمضي مشرق يبدأ بـ{top_str}، كنسمة صباحية على شاطئ المتوسط.",
            f"طاقة حمضية نابضة تنطلق من {top_str}، لتمنحك بداية يوم مفعمة بالحيوية.",
            f"عطر حمضي منعش يفتتح بـ{top_str}، ويعد بتجربة منعشة طوال اليوم.",
            f"إشراقة حمضية مشمسة تبدأ بـ{top_str}، في تركيبة تنبض بالنشاط والحياة.",
        ],
        "جلدي": [
            f"عطر جلدي جريء يفتتح بـ{top_str}، ليجسد القوة والأناقة الكلاسيكية.",
            f"تركيبة جلدية فاخرة تبدأ بـ{top_str}، وتتكشف عن شخصية واثقة ومؤثرة.",
            f"من عالم الجلود النبيلة، عطر ينطلق بـ{top_str} في مقدمة تأسر الحواس.",
            f"حضور جلدي ملكي يستهل بـ{top_str}، ويعبر عن ذوق رفيع لا يُضاهى.",
            f"أناقة جلدية استثنائية تنفتح على {top_str}، في رحلة عطرية تفيض بالجرأة.",
        ],
    }
    default = [
        f"عطر فاخر يفتتح بـ{top_str}، في تجربة عطرية استثنائية تليق بذوقك الرفيع.",
        f"تركيبة راقية تنطلق من {top_str}، لتمنحك حضوراً مميزاً في كل لحظة.",
        f"إبداع عطري فريد يبدأ بلمسات {top_str}، ويتطور نحو عمق آسر لا يُنسى.",
        f"عطر يجسد الفخامة بمقدمة من {top_str}، ويعد بتجربة حسية لا مثيل لها.",
        f"رحلة عطرية مميزة تستهل بـ{top_str}، وتتكشف عن طبقات من الجمال والرقي.",
    ]
    for key, tmps in templates.items():
        if key in f:
            return tmps[idx]
    return default[idx]


def _build_notes_section(layer_title: str, notes_list: List[str], icon: str) -> str:
    if not notes_list:
        return ""
    described = [_describe_note(n) for n in notes_list[:4]]
    extra = notes_list[4:]
    text = "، ".join(described)
    if extra:
        text += "، مع لمسات " + " و".join(extra[:2])
    text += "."
    return (
        f'<h3 style="font-size:15px;color:#1a1a1a;border-bottom:1px solid #f5ecd4;'
        f'padding-bottom:6px;margin:4px 0 4px;">{icon} {layer_title}</h3>'
        f'<p style="line-height:1.5;color:#333;font-size:15px;margin:0 0 4px;">{text}</p>'
    )


def _build_expert_tip(family: str, top: List[str], base: List[str]) -> str:
    f = (family or "").lower()
    seed = hashlib.md5(("".join(top[:1]) + "".join(base[:1])).encode()).hexdigest()
    idx = int(seed[:2], 16) % 4
    tips = [
        "رشّه على نقاط النبض — المعصم وخلف الأذن — واتركه يتفاعل مع حرارة بشرتك ليكشف عن أبعاده الحقيقية.",
        "للحصول على أقصى أداء، رشّه على بشرة مرطبة بعد الاستحمام مباشرة، واترك العطر يتكشف بهدوء.",
        "طبّقه على الملابس من مسافة 20 سم للحصول على فوّاحية ممتدة دون أن تطغى الرائحة.",
        "ضعه صباحاً واتركه يتطور خلال اليوم — ستكتشف طبقات جديدة مع كل ساعة تمر.",
    ]
    if "شرقي" in f or "عنبر" in f:
        tips = [
            "دعه يستقر لبضع دقائق بعد الرش؛ حينها تتكشف طبقاته العميقة وتألقه الحقيقي.",
            "رشّه على نقاط النبض الدافئة — المعصم وخلف الأذن — ليتفاعل مع حرارة بشرتك ويكشف عن غناه.",
            "ضعه على بشرة مرطبة بكريم بلا رائحة، وسترى كيف تتضاعف فوّاحيته وثباته.",
            "رشّه قبل ارتداء ملابسك بخمس دقائق، ليختلط بحرارة جسمك ويمنحك هالة عطرية آسرة.",
        ]
    return tips[idx]


def _build_why_choose(brand: str, concentration: str, top: List[str], heart: List[str],
                      base: List[str], family: str) -> List[str]:
    # نقطتا الماركة والتركيز تُدرَجان فقط عند معرفتهما — لا ادّعاء عن مجهول.
    points = []
    if brand:
        points.append(f"من دار {brand} — ضمان الجودة والتميز في كل قطرة.")
    if concentration:
        points.append(f"تركيز {concentration} يمنحك أداءً متوازناً وثباتاً ممتازاً على البشرة.")
    all_notes = (top or [])[:2] + (heart or [])[:1] + (base or [])[:1]
    if all_notes:
        note_str = " و".join(all_notes[:3])
        points.append(f"تركيبة فريدة تجمع بين {note_str} لإطلالة عطرية لا تُنسى.")
    f = (family or "").lower()
    if "شرقي" in f or "عنبر" in f:
        points.append("مثالي للمساء والمناسبات الخاصة — يمنحك حضوراً دافئاً وآسراً.")
    elif "مائي" in f or "حمضي" in f:
        points.append("مثالي للنهار والخروجات — يمنحك انتعاشاً يدوم طوال اليوم.")
    elif "زهري" in f:
        points.append("مثالي للقاءات والمناسبات الأنيقة — يمنحك حضوراً ناعماً ومميزاً.")
    else:
        points.append("مناسب لمختلف المناسبات — يمنحك حضوراً أنيقاً في كل إطلالة.")
    return points


# ── أدوات قراءة dict المنتج ─────────────────────────────────────────────────
def _first(product: Dict, keys: tuple, default: str = "") -> str:
    for k in keys:
        v = product.get(k)
        if v is not None:
            s = str(v).strip()
            if s and s.lower() not in ("nan", "none", "غير متوفر", "غير محدد"):
                return s
    return default


def _split_notes(raw: Any) -> List[str]:
    """يحوّل نص النوتات (مفصول بـ،/و/؛/سطر) إلى قائمة نظيفة بلا تكرار."""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        s = str(raw or "").strip()
        if not s or s.lower() in ("nan", "none", "غير متوفر", "غير محدد"):
            return []
        parts = re.split(r"[،,؛\n/]+|\sو\s", s)
    out, seen = [], set()
    for p in parts:
        t = p.strip(" .،-")
        if t and t.lower() not in ("nan", "none", "غير متوفر") and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ── الدالة الرئيسية ─────────────────────────────────────────────────────────
def _links_block(brand_name: str, category_name: str = "", gender: str = "") -> str:
    """كتلة «اكتشف المزيد» بروابط mahwous.com الحقيقية (lookup من السايت ماب).

    آمن: عند تعذّر اللوكاب يتدرّج إلى رابط المتجر العام فلا يبقى بلا روابط.
    """
    brand_url = cat_url = None
    cat_label = category_name
    try:
        from utils import mahwous_links as _ml
        if brand_name:
            brand_url = _ml.lookup_brand_url(brand_name)
        if category_name:
            cat_url = _ml.lookup_category_url(category_name)
        if not cat_url and gender:
            cat_url = _ml.lookup_category_url_for_perfume(gender)
            cat_label = ("العطور النسائية" if "نسائي" in gender
                         else "العطور الرجالية" if "رجال" in gender else "العطور")
    except Exception:
        pass
    _a = 'style="color:#8b6914;text-decoration:none;font-size:14px;"'
    lis = []
    if brand_url and brand_name:
        lis.append(f'<li style="margin:1px 0;"><a href="{brand_url}" {_a}>تصفّح ماركة {brand_name} ←</a></li>')
    if cat_url and cat_label:
        lis.append(f'<li style="margin:1px 0;"><a href="{cat_url}" {_a}>تصفّح {cat_label} ←</a></li>')
    lis.append(f'<li style="margin:1px 0;"><a href="https://mahwous.com" {_a}>المزيد من متجر مهووس ←</a></li>')
    return (
        '<div style="margin:3px 0;padding:6px 12px;background:#f9f9f9;border-radius:8px;">'
        '<h4 style="font-size:15px;color:#1a1a1a;margin:0 0 3px;font-weight:600;">🔗 اكتشف المزيد</h4>'
        f'<ul style="margin:0;padding:0 10px;list-style:none;">{"".join(lis)}</ul></div>'
    )


_CARE_USAGE = {
    "hair": ("عناية بالشعر", "ضعه على شعر رطب نظيف، دلّك فروة الرأس والأطراف، اتركه المدة الموصى بها ثم اشطفه جيداً."),
    "body": ("عناية بالجسم", "وزّعه على بشرة نظيفة بحركات دائرية لطيفة حتى يُمتَص بالكامل."),
    "makeup": ("مستحضر تجميل", "طبّقيه بأدوات نظيفة للحصول على نتيجة متقنة، وأزيليه بمنظّف مناسب."),
    "care": ("مستحضر عناية", "اتبع التعليمات المدوّنة على العبوة، واستخدم الكمية المناسبة بانتظام لأفضل نتيجة."),
}


def _care_subtype(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("مكياج", "أحمر شفاه", "ماسكرا", "كحل", "فاونديشن", "makeup", "lipstick", "mascara")):
        return "makeup"
    if any(k in t for k in ("شعر", "شامبو", "بلسم", "كيراتين", "بروتين", "علاج الزيت", "حمام", "hair")):
        return "hair"
    if any(k in t for k in ("جسم", "بشره", "بشرة", "لوشن", "زبده", "زبدة", "مرطب", "body")):
        return "body"
    return "care"


def _golden_care_html(product_name: str, brand: str, category: str, size: str, product: Dict) -> str:
    """قالب مهووس الذهبي لمنتجات العناية/المكياج — نفس فخامة العطر، محتوى عناية،
    بلا أيّ لغة عطور (لا EDP/نقاط نبض)، وروابط mahwous حقيقية."""
    import re as _re2

    short = (product_name or "منتج").strip()
    sub = _care_subtype(f"{product_name} {category}")
    type_label, usage = _CARE_USAGE[sub]
    size_label = f"{size} مل" if str(size).isdigit() else (str(size or "").strip())
    br = (brand or "").strip()  # مجهولة ⇒ "" فيُسقَط سطرها بدل تلفيق «ماركة عالمية»

    ingredients = _first(product, ("المكونات", "ingredients", "active_ingredients"), "")
    if ingredients:
        ing_html = f'<p style="margin:0;font-size:14.5px;color:#333;line-height:1.6;">{ingredients}</p>'
    else:
        ing_html = ('<p style="margin:0;font-size:14.5px;color:#333;line-height:1.6;">تركيبة غنية بمكوّنات '
                    'فعّالة تعتني بحاجتك وتمنحك نتيجة ملموسة مع الاستخدام المنتظم.</p>')

    details = (
        '<div style="background:#faf6ec;padding:6px 12px;border-right:3px solid #ddb562;margin:0 0 4px;">'
        + (f'<p style="margin:3px 0;font-size:15px;color:#1a1a1a;"><strong>الماركة:</strong> {br}</p>' if br else "")
        + f'<p style="margin:3px 0;font-size:15px;color:#1a1a1a;"><strong>النوع:</strong> {type_label}</p>'
        + (f'<p style="margin:3px 0;font-size:15px;color:#1a1a1a;"><strong>الحجم:</strong> {size_label}</p>' if size_label else "")
        + '</div>'
    )
    _b = '<span style="position:absolute;right:0;color:#ddb562;">✦</span>'
    _li = 'style="margin:1px 0;color:#333;font-size:14.5px;padding-right:18px;position:relative;"'
    parts = [
        f'<h2 style="margin:0 0 6px;color:#1a1a1a;font-size:20px;font-weight:800;">{short} | توصيل سريع في السعودية</h2>',
        f'<p style="margin:0 0 6px;font-size:15px;color:#333;line-height:1.6;">{short}'
        + (f' من {br}' if br else '')
        + f' — {type_label} فاخر '
        'بجودة عالمية، مصمَّم ليمنحك عناية فعّالة ونتيجة تدوم، بمعايير متجر مهووس.</p>',
        '<h3 style="font-size:16px;color:#1a1a1a;margin:6px 0 3px;font-weight:700;">تفاصيل المنتج</h3>', details,
        '<h3 style="font-size:16px;color:#b8860b;margin:6px 0 3px;font-weight:700;">المكوّنات الفعّالة</h3>', ing_html,
        '<p style="background:#f5ecd4;padding:6px 12px;font-size:15px;line-height:1.5;color:#4a3a1a;font-style:italic;'
        f'margin:6px 0;border-right:3px solid #ddb562;"><strong>همسة من خبير مهووس:</strong> لأفضل نتيجة مع {short}، '
        + (f'استخدمه بانتظام واتبع طريقة الاستخدام؛ المنتج الأصلي من {br} يمنحك فرقاً واضحاً.</p>'
           if br else 'استخدمه بانتظام واتبع طريقة الاستخدام لتلمس فرقاً واضحاً.</p>'),
        '<div style="background:#1a1a1a;color:#f5ecd4;padding:6px 12px;margin:6px 0;text-align:center;">'
        '<p style="margin:0;font-size:14px;letter-spacing:0.4px;line-height:1.5;">'
        'ضمان الأصالة 100% — مصادر موثوقة عالمية — تغليف هدية مجاني</p></div>',
        '<div style="background:#faf6ec;padding:6px 12px;border-right:3px solid #ddb562;margin:6px 0;">'
        f'<h3 style="font-size:17px;color:#1a1a1a;margin:0 0 3px;font-weight:700;">لماذا تختار {short}؟</h3>'
        '<ul style="margin:0;padding:0 10px;list-style:none;">'
        + (f'<li {_li}>{_b}منتج أصلي 100% من {br}</li>' if br else f'<li {_li}>{_b}منتج أصلي 100%</li>')
        + f'<li {_li}>{_b}{type_label} بجودة عالمية وسعر منافس</li>'
        + f'<li {_li}>{_b}شحن سريع وتغليف هدية مجاني من مهووس</li></ul></div>',
        '<h3 style="font-size:16px;color:#1a1a1a;margin:6px 0 3px;font-weight:700;">طريقة الاستخدام</h3>',
        f'<p style="margin:0 0 6px;font-size:14.5px;color:#444;line-height:1.6;">{usage} للاستخدام الخارجي فقط.</p>',
        '<div style="margin:3px 0;"><h3 style="font-size:15px;color:#1a1a1a;border-bottom:1px solid #f5ecd4;'
        'padding-bottom:6px;margin:0 0 3px;font-weight:700;">الأسئلة الشائعة</h3>'
        '<h4 style="font-size:16px;color:#1a1a1a;margin:4px 0;font-weight:600;">هل المنتج أصلي 100%؟</h4>'
        '<p style="line-height:1.5;color:#444;font-size:14.5px;margin:0 0 4px;">نعم، جميع منتجات مهووس أصلية 100% '
        'ومستوردة من وكلاء معتمدين، وتصلك في عبوتها الأصلية مع ضمان الجودة.</p>'
        '<h4 style="font-size:16px;color:#1a1a1a;margin:4px 0;font-weight:600;">هل يناسب الاستخدام المنتظم؟</h4>'
        '<p style="line-height:1.5;color:#444;font-size:14.5px;margin:0 0 4px;">نعم، تركيبته مناسبة للاستخدام حسب '
        'التعليمات المدوّنة على العبوة.</p></div>',
        _links_block(brand, category),
        '<p style="text-align:center;font-size:15px;color:#1a1a1a;margin:6px 0 2px;font-weight:600;">'
        'اطلبه قبل نفاد الكمية — شحن سريع لجميع المدن</p>',
        f'<p style="font-size:12px;color:#999;margin:2px 0;">كلمات البحث: {short}'
        + (f' | {br}' if br else '') + '</p>',
    ]
    html = f'<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;color:#333;line-height:1.5;">{"".join(parts)}</div>'
    return _re2.sub(r"\s{2,}", " ", html.replace("\n", " ")).strip()


def build_golden_description(product: Dict) -> str:
    """يبني وصف مهووس الذهبي الكامل من dict المنتج. حتمي، بلا API.

    يقرأ الاسم/الماركة/النوتات/العائلة/الاسم الإنجليزي/السنة من حقول المنتج
    المُثراة؛ ويتدرّج للنسخة العامة (بلا هرم/بطاقة) عند غياب النوتات.
    """
    product_name = _first(
        product,
        ("أسم المنتج", "اسم المنتج", "المنتج", "منتج_المنافس", "name", "product_name", "title"),
        "عطر فاخر",
    )
    brand_col = _first(product, ("الماركة", "الماركة_الرسمية", "brand", "brand_name"), "")

    # ── نوع المنتج: عناية/مكياج ⇒ وصف عناية (لا لغة عطور: لا EDP ولا نقاط نبض) ──
    #   ضمان مزدوج بجانب المُصنِّف: حتى لو تسرّب منتج عناية، وصفه يتبع نوعه.
    _category = _first(
        product,
        ("تصنيف_المنتج", "التصنيف_الرسمي", "اسم التصنيف", "تصنيف المنتج", "category", "category_name"),
        "",
    )
    try:
        from utils.salla_shamel_export import _detect_product_type
        _ptype = _detect_product_type(product_name, _category)
        if _ptype in ("care", "makeup"):
            return _golden_care_html(
                product_name, brand_col, _category, _extract_size(product_name), product,
            )
    except Exception:
        pass

    top = _split_notes(_first(product, ("top_notes", "مقدمة_العطر", "الافتتاحية", "النفحات العليا"), ""))
    heart = _split_notes(_first(product, ("heart_notes", "middle_notes", "قلب_العطر", "القلب"), ""))
    base = _split_notes(_first(product, ("base_notes", "قاعدة_العطر", "القاعدة"), ""))
    family = _first(product, ("العائلة_العطرية", "fragrance_family", "family"), "")
    eng_name = _first(product, ("english_name", "الاسم_الانجليزي", "name_en", "eng_name", "اسم_بالانجليزي"), "")
    year = _first(product, ("year", "سنة_الإصدار", "release_year", "سنة_الاصدار"), "")

    brand = _extract_brand(product_name, brand_col)
    # الجنس من أقوى إشارة: التصنيف (وسم Algolia، 95.6% تغطية) ← الحقل الصريح ←
    # الاسم. المجهول يبقى "" فيُسقَط سطر الجنس بدل تلفيق «للجنسين» (إصلاح الجلسة 18).
    from utils.gender_resolver import resolve_gender
    gender = resolve_gender(
        name=product_name, category=_category,
        explicit=_first(product, ("الجنس", "gender", "gender_hint"), ""),
    )
    conc = _extract_concentration(product_name)
    size = _extract_size(product_name)

    has_notes = bool(top or heart or base)
    occasion = _get_occasion(family, gender)
    season = _get_season(family)
    short_name = product_name.replace("عطر ", "").strip() if product_name else ""

    # ═══ 1: TITLE ═══
    title_h2 = f"{short_name} | توصيل سريع في السعودية"

    # ═══ 2: OPENING ═══
    if has_notes:
        opening = _build_opening(family, top, product_name)
    else:
        opening = "عطر فاخر يجسد الأناقة والتميز، مصمم لمن يبحث عن تجربة عطرية استثنائية تليق بذوقه الرفيع."

    # ═══ 3: PRODUCT DETAILS ═══
    # سطر الجنس يُطبع فقط عند إشارة حقيقية — مجهول الجنس ("") يُسقَط بلا تلفيق.
    _gender_line = (
        f'<p style="margin:3px 0;font-size:15px;color:#1a1a1a;"><strong>الجنس:</strong> {gender}</p>'
        if gender else ""
    )
    # الماركة/التركيز/الحجم تتبع قاعدة الجنس نفسها: المجهول يُسقَط سطره ولا يُلفَّق.
    _row = 'style="margin:3px 0;font-size:15px;color:#1a1a1a;"'
    _brand_line = f'<p {_row}><strong>الماركة:</strong> {brand}</p>' if brand else ""
    _conc_line = f'<p {_row}><strong>التركيز:</strong> {conc}</p>' if conc else ""
    _size_line = f'<p {_row}><strong>الحجم:</strong> {size}</p>' if size else ""
    details_block = (
        '<div style="background:#faf6ec;padding:6px 12px;border-right:3px solid #ddb562;margin:0 0 4px;">'
        f'{_brand_line}'
        f'{_gender_line}'
        f'{_conc_line}'
        f'{_size_line}'
        f'<p {_row}><strong>المناسبة المثالية:</strong> {occasion}</p>'
        '</div>'
    )

    # ═══ 4: ABOUT PARAGRAPH ═══
    if has_notes:
        key_notes = (top[:2] + heart[:1] + base[:1])
        note_mention = " و".join(key_notes[:3])
        about = (
            f"عطر {short_name} يجسد الفخامة بتركيبة آسرة، مصمم لمن يبحث عن حضور فريد. "
            f"يتميز هذا العطر بمزيج غني من {note_mention} يروي قصة من الجاذبية والتميز، "
            f"مما يجعله خياراً مثالياً للمناسبات الراقية. إنه يترك أثراً لا يُمحى، ويعكس "
            f"شخصية مفعمة بالأصالة والرقي."
        )
    else:
        about = (
            f"عطر {short_name} يجسد الأناقة والتميز في تركيبة فاخرة، مصمم لمن يبحث عن "
            f"تجربة عطرية راقية. إنه يترك أثراً مميزاً ويعكس شخصية مفعمة بالذوق الرفيع."
        )

    # ═══ 5: FRAGRANCE JOURNEY ═══
    journey = ""
    if has_notes:
        journey += _build_notes_section("الافتتاحية — الانطباع الأول", top, "🔝")
        journey += _build_notes_section("القلب العطري — روح العطر", heart, "❤️")
        journey += _build_notes_section("القاعدة — البصمة التي تبقى", base, "🪵")

    # ═══ 6: EXPERT TIP ═══
    if has_notes:
        tip_text = _build_expert_tip(family, top, base)
    else:
        tip_text = "رشّه على نقاط النبض — المعصم وخلف الأذن — واتركه يتفاعل مع حرارة بشرتك ليكشف عن أبعاده الحقيقية."
    expert_tip = (
        '<p style="background:#f5ecd4;padding:6px 12px;font-size:15px;line-height:1.5;color:#4a3a1a;'
        f'font-style:italic;margin:3px 0;border-right:3px solid #ddb562;"><strong>همسة من خبير مهووس:</strong> {tip_text}</p>'
    )

    # ═══ 7: GUARANTEE BAR ═══
    guarantee = (
        '<div style="background:#1a1a1a;color:#f5ecd4;padding:6px 12px;margin:3px 0;text-align:center;">'
        '<p style="margin:0;font-size:14px;letter-spacing:0.4px;line-height:1.5;">'
        'ضمان الأصالة 100% — مصادر موثوقة عالمية — تغليف هدية مجاني</p></div>'
    )

    # ═══ 8: PERFUME CARD ═══
    card = ""
    if has_notes:
        card_items = f'<li style="margin:1px 0;"><strong>العائلة العطرية:</strong> {family or "غير متوفر"}</li>'
        if eng_name:
            card_items += f'<li style="margin:1px 0;"><strong>الاسم بالإنجليزية:</strong> {eng_name}</li>'
        if year and year != "-":
            card_items += f'<li style="margin:1px 0;"><strong>سنة الإصدار:</strong> {year}</li>'
        # سطر التوثيق يُطبع فقط عند تأريض حقيقي. مسار «Fragrantica» ليس كشطاً
        # للموقع بل نداء Gemini مؤرَّض، وله احتياطيان بلا تأريض نوتاتهما من
        # ذاكرة النموذج (engines/ai_engine.py:766-775 — تعليقه نفسه: «قد تُلفَّق»).
        # ادّعاء «موثّقة من Fragrantica» فوق نوتات غير مؤرَّضة تلفيقُ مصدرٍ أمام
        # العميل، وهو أثقل من حقل مجهول. المجهول يُسقَط سطره.
        if product.get("_notes_grounded") is True:
            card_items += '<li style="margin:1px 0;color:#8a7a4a;font-size:13px;">معلومة موثّقة من Fragrantica.</li>'
        card = (
            '<div style="background:#faf6ec;border-right:3px solid #ddb562;padding:6px 12px;margin:3px 0;">'
            '<h3 style="font-size:15px;color:#1a1a1a;border-bottom:1px solid #f5ecd4;padding-bottom:6px;'
            'margin:0 0 3px;font-weight:700;">بطاقة العطر الموثّقة</h3>'
            f'<ul style="padding:0 10px 0 0;margin:0;list-style:none;">{card_items}</ul></div>'
        )

    # ═══ 9: WHY CHOOSE ═══
    why_points = _build_why_choose(brand, conc, top, heart, base, family)
    why_lis = "".join(
        f'<li style="margin:1px 0;line-height:1.5;color:#333;font-size:14.5px;padding-right:18px;'
        f'position:relative;"><span style="position:absolute;right:0;color:#ddb562;">✦</span>{p}</li>'
        for p in why_points
    )
    why_section = (
        '<div style="background:#faf6ec;padding:6px 12px;border-right:3px solid #ddb562;margin:3px 0;">'
        f'<h3 style="font-size:17px;color:#1a1a1a;margin:0 0 3px;font-weight:700;">لماذا تختار {short_name}؟</h3>'
        f'<ul style="margin:0;padding:0 10px;list-style:none;">{why_lis}</ul></div>'
    )

    # ═══ 10: LINKS — روابط mahwous.com حقيقية (ماركة + تصنيف) من السايت ماب ═══
    links = _links_block(brand, _category, gender)

    # ═══ 11: FAQ ═══
    faq = (
        '<div style="margin:3px 0;"><h3 style="font-size:15px;color:#1a1a1a;border-bottom:1px solid #f5ecd4;'
        'padding-bottom:6px;margin:0 0 3px;font-weight:700;">الأسئلة الشائعة</h3>'
        '<h4 style="font-size:16px;color:#1a1a1a;margin:4px 0 4px;font-weight:600;">هل المنتج أصلي 100%؟</h4>'
        '<p style="line-height:1.5;color:#444;font-size:14.5px;margin:0 0 4px;">نعم، جميع المنتجات في متجر مهووس '
        'أصلية 100% ومستوردة من الوكلاء المعتمدين. يصلك المنتج في عبوته الأصلية المغلفة مع ضمان الجودة.</p>'
        '<h4 style="font-size:16px;color:#1a1a1a;margin:4px 0 4px;font-weight:600;">هل يتوفر تغليف هدايا؟</h4>'
        '<p style="line-height:1.5;color:#444;font-size:14.5px;margin:0 0 4px;">نعم، نوفر خدمة تغليف هدايا فاخرة. '
        'يمكنك طلبها عند إتمام الشراء لتصلك الهدية بإطلالة أنيقة.</p>'
        '<h4 style="font-size:16px;color:#1a1a1a;margin:4px 0 4px;font-weight:600;">ما أنسب وقت لارتداء هذا العطر؟</h4>'
        f'<p style="line-height:1.5;color:#444;font-size:14.5px;margin:0 0 4px;">يتألق هذا العطر بشكل خاص في {season} '
        'بفضل مكوناته التي تتناغم مع أجواء هذا الموسم. رشّه بثقة واستمتع بأفضل أداء.</p></div>'
    )

    # ═══ 12: KEYWORDS + CLOSING ═══
    kw_parts = [short_name, brand]
    if eng_name:
        kw_parts.append(eng_name)
    keywords = " | ".join(p for p in kw_parts if p)  # ماركة فارغة لا تترك فاصلاً يتيماً
    closing = (
        '<p style="text-align:center;font-size:15px;color:#1a1a1a;margin:2px 0 2px;font-weight:600;">'
        'اطلبه قبل نفاد الكمية — شحن سريع لجميع المدن</p>'
    )
    kw_block = (
        f'<p style="font-size:13px;color:#666;margin-top:12px;line-height:1.6;"><strong>كلمات البحث:</strong> {keywords}</p>'
    )

    # ═══ ASSEMBLE ═══
    html = (
        f'<div data-product-description="true" dir="rtl" style="text-align:center;padding:6px 0 6px;'
        f'border-bottom:1px solid #ddb562;margin:0 0 4px;">'
        f'<h2 style="font-size:18px;font-weight:700;margin:0;color:#1a1a1a;letter-spacing:0.3px;'
        f'line-height:1.5;">{title_h2}</h2></div>'
        f'<p style="font-style:italic;font-size:16px;line-height:1.5;color:#333;padding:3px 10px 3px 0;'
        f'border-right:2px solid #ddb562;margin:0 0 4px;">{opening}</p>'
        f'{details_block}'
        f'<p style="line-height:1.5;color:#333;font-size:15px;margin:0 0 4px;">{about}</p>'
        f'{journey}{expert_tip}{guarantee}{card}{why_section}{links}{faq}{closing}{kw_block}'
    )
    return html
