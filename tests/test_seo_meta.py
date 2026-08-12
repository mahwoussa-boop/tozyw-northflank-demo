"""tests/test_seo_meta.py — توليد عنوان/وصف SEO ضمن حدود سلة.

يضمن: الحقلان ممتلئان دائماً، ضمن ≤60/≤160، يحترمان حقول الـAI إن وُجدت،
ولا يكرّران الماركة المذكورة أصلاً في الاسم.
"""
from utils.seo_meta import (
    DESC_MAX,
    TITLE_MAX,
    build_seo_description,
    build_seo_slug,
    build_seo_title,
    seo_fields,
)


# ════════════════════════════════════════════════════════════════════
#  build_seo_slug — رابط السيو الإنجليزي
# ════════════════════════════════════════════════════════════════════
def test_slug_uses_ai_translation_and_appends_size() -> None:
    ai = lambda name, brand: "Schwarzkopf_Bonawell_Hot_Oil_Treatment_Wheat"
    s = build_seo_slug("بوناويل علاج الزيت الساخن بالقمح من شوارزكوف 225مل",
                       brand="Schwarzkopf", _ai=ai)
    assert s == "Schwarzkopf_Bonawell_Hot_Oil_Treatment_Wheat_225ml"  # AI + الحجم


def test_slug_is_ascii_underscores_only() -> None:
    s = build_seo_slug("عطر", brand="Dior", _ai=lambda n, b: "Dior Sauvage EDP!! 100ml")
    assert all(c.isascii() for c in s)
    assert " " not in s and "!" not in s


def test_slug_fallback_transliterates_when_no_ai() -> None:
    # AI يُرجع فارغاً ⇒ حرفنة حتمية (ليس فارغاً، ASCII، فيه الحجم)
    s = build_seo_slug("ديور سوفاج 100مل", brand="Dior", _ai=lambda n, b: "")
    assert s and all(c.isascii() for c in s)
    assert "100ml" in s
    assert s.startswith("Dior")


def test_slug_empty_name() -> None:
    assert build_seo_slug("", brand="X", _ai=lambda n, b: "") == ""


def test_slug_never_empty_for_nonempty_name() -> None:
    """ضمان عقد Make v3: «رابط السيو» حامل لاسم الإنشاء — لا يفرغ أبداً لاسم غير فارغ.

    أسماء شاذّة (كلها كلمات وقف/أحرف تُحرفَن لفراغ) كانت قد تُنتج slug فارغاً
    فيُسقطه فلتر الحمولة ⇒ يفشل إنشاء المنتج في سلة. الآن يتدرّج لبصمة مضمونة.
    """
    for nm in ("مل", "من مل", "ء", "؟!،", "the of"):
        s = build_seo_slug(nm, brand="", _ai=lambda n, b: "")
        assert s, f"slug فارغ لاسم غير فارغ: {nm!r}"
        assert all(c.isascii() for c in s)
        # بلا مسافات — Make يحوّل _→مسافة عند الإنشاء؛ فالـslug ذاته بلا فراغات.
        assert " " not in s


def test_title_within_limit_and_has_store() -> None:
    t = build_seo_title("ديور سوفاج او دو بارفيوم 100مل", "Dior")
    assert len(t) <= TITLE_MAX
    assert "مهووس" in t


def test_title_long_name_clamped_no_word_cut() -> None:
    long_name = "عطر فاخر بمكونات نادرة " * 6
    t = build_seo_title(long_name, "Brand")
    assert len(t) <= TITLE_MAX
    assert not t.endswith(" ")


def test_brand_not_duplicated_when_in_name() -> None:
    t = build_seo_title("Dior Sauvage EDP 100ml", "Dior")
    assert t.lower().count("dior") == 1


def test_brand_appended_when_absent() -> None:
    t = build_seo_title("سوفاج 100مل", "Dior")
    assert "Dior" in t


def test_description_within_limit() -> None:
    d = build_seo_description("ديور سوفاج", "Dior", "عطري خشبي")
    assert len(d) <= DESC_MAX
    assert "مهووس" in d


def test_description_very_long_name_clamped() -> None:
    d = build_seo_description("منتج " * 80, "ماركة", "عائلة")
    assert len(d) <= DESC_MAX


def test_seo_fields_keys_present_and_nonempty() -> None:
    out = seo_fields({"أسم المنتج": "لطافة اخلاص 100مل", "الماركة": "Lattafa"})
    assert set(out) == {"عنوان السيو", "وصف السيو"}
    assert out["عنوان السيو"] and out["وصف السيو"]
    assert len(out["عنوان السيو"]) <= TITLE_MAX
    assert len(out["وصف السيو"]) <= DESC_MAX


def test_seo_fields_honors_ai_values() -> None:
    out = seo_fields({
        "name": "x", "seo_title": "عنوان من الذكاء الصناعي",
        "seo_description": "وصف من الذكاء الصناعي يشجّع النقر.",
    })
    assert out["عنوان السيو"] == "عنوان من الذكاء الصناعي"
    assert out["وصف السيو"] == "وصف من الذكاء الصناعي يشجّع النقر."


def test_seo_fields_clamps_overlong_ai_values() -> None:
    out = seo_fields({
        "name": "x",
        "seo_title": "ع" * 120,
        "seo_description": "و" * 300,
    })
    assert len(out["عنوان السيو"]) <= TITLE_MAX
    assert len(out["وصف السيو"]) <= DESC_MAX


def test_seo_fields_explicit_overrides() -> None:
    out = seo_fields({"أسم المنتج": "اسم مهمل"}, name="سوفاج 100مل", brand="Dior")
    assert "Dior" in out["عنوان السيو"]
