# -*- coding: utf-8 -*-
"""tests/test_gender_resolver.py — محلّل الجنس: أقوى إشارة، بلا اختلاق «للجنسين».

يثبت الإصلاح الجوهري للجلسة 18: التصنيف (وسم Algolia) يُقرأ كإشارة جنس، والمجهول
يبقى مجهولاً ("") لا يُلفَّق «للجنسين».
"""
from utils.gender_resolver import gender_from_text, resolve_gender


# ── gender_from_text: من التصنيف (مصدر الحقيقة) ────────────────────────
def test_category_male() -> None:
    assert gender_from_text("جمال وعناية > العطور والعود > عطور رجالية") == "رجالي"


def test_category_female() -> None:
    assert gender_from_text("جمال وعناية > العطور والعود > عطور نسائية") == "نسائي"


def test_category_unisex_explicit() -> None:
    assert gender_from_text("جمال وعناية > العطور والعود > عطور للجنسين") == "للجنسين"


# ── من الاسم ───────────────────────────────────────────────────────────
def test_name_arabic_male() -> None:
    assert gender_from_text("عطر ديور سوفاج للرجال او دو بارفيوم 100مل") == "رجالي"


def test_name_english_men() -> None:
    assert gender_from_text("Dior Sauvage for men EDP 100ml") == "رجالي"


def test_name_english_women_not_confused_by_men_substring() -> None:
    # «women» تحوي «men» — يجب ألّا تُصنَّف رجالي
    assert gender_from_text("Chanel Coco Mademoiselle for women") == "نسائي"


def test_name_femme() -> None:
    assert gender_from_text("Lancome La Vie Est Belle Femme") == "نسائي"


# ── المجهول يبقى مجهولاً (جوهر الإصلاح) ────────────────────────────────
def test_unknown_returns_empty_not_unisex() -> None:
    # لا كلمة جنس ⇒ "" (مجهول)، لا «للجنسين» ملفّقة
    assert gender_from_text("عطر فخم فاخر بتركيبة آسرة") == ""


def test_empty_input_returns_empty() -> None:
    assert gender_from_text("") == ""
    assert gender_from_text(None) == ""


def test_conflicting_signals_return_empty() -> None:
    # اسم يحمل الإشارتين ⇒ لا حسم ⇒ "" (لا اختلاق)
    assert gender_from_text("عطر رجالي ونسائي") == ""


# ── resolve_gender: ترتيب الأولوية ─────────────────────────────────────
def test_category_beats_name() -> None:
    # التصنيف «رجالية» يفوز رغم خلوّ الاسم من إشارة
    assert resolve_gender(
        name="عطر فخم بلا إشارة", category="عطور رجالية",
    ) == "رجالي"


def test_explicit_used_when_no_category() -> None:
    assert resolve_gender(name="عطر فخم", explicit="نسائي") == "نسائي"


def test_name_used_as_last_resort() -> None:
    assert resolve_gender(name="عطر نسائي أنيق") == "نسائي"


def test_all_unknown_returns_empty() -> None:
    assert resolve_gender(name="عطر فخم", category="العطور والعود", explicit="") == ""


def test_category_none_safe() -> None:
    # وسائط None لا تُسقِط الدالة
    assert resolve_gender(name=None, category=None, explicit=None) == ""
