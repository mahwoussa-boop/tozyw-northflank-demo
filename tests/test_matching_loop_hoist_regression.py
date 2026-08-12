"""اختبار انحدار خصامي — بند هـ (2026-07-22): تنظيف حلقة المطابقة في CompIndex.search().

التغيير: (1) عضوية «مجموعات Flanker» لمنتجنا (`_group_in_text(gi, o_n_low)`) كانت
تُعاد حسابها لكل مرشّح رغم ثبات `o_n_low` طوال البحث — رُفعت خارج حلقة المرشّحين.
(2) قاموس `_MUST_MATCH_WORDS` كان يُعاد بناؤه كقاموس حرفي جديد لكل مرشّح — رُفع
لثابت وحدة. كلا التغييرين لا يمسّان أي قيمة أو منطق قرار — فقط يتفاديان إعادة
حساب/بناء نفس الشيء. هذه الاختبارات تثبت أن قرار القبول/الرفض (لا الأداء) لم
يتغيّر لكل من الحالتين، إيجاباً وسلباً.
"""
import pandas as pd

from engines.engine import CompIndex, normalize

_BRAND = "ماركة اختبار حصرية"


def _idx(names: list[str]) -> CompIndex:
    df = pd.DataFrame({
        "name": names,
        "brand": [_BRAND] * len(names),
        "السعر": [100.0] * len(names),
    })
    return CompIndex(df, name_col="name", id_col="", comp_name="CompA")


def _search(idx: CompIndex, our_name: str):
    # الاستدعاء الحقيقي (engine.py:3048) يمرّر normalize(product) دائماً، لا الاسم
    # الخام — نطابق نفس العقد هنا كي لا نصطدم بعدم تناظر الخام↔المطبَّع في فحص
    # Flanker (موثَّق في تعليق v35 داخل search()).
    return idx.search(
        our_norm=normalize(our_name), our_br=_BRAND, our_sz=100,
        our_tp="", our_gd="", our_pline="",
    )


def test_flanker_mismatch_rejects_same_brand_candidate():
    """منتجنا يحمل «سبورت» (مجموعة Flanker) والمنافس لا يحملها ⇒ رفض رغم تشابه الاسم الأساسي."""
    idx = _idx(["عطر اختبار حصري 100 مل"])  # بلا "سبورت"
    assert _search(idx, "عطر اختبار حصري سبورت 100 مل") == []


def test_flanker_match_accepts_when_both_sides_share_group():
    """كلا الطرفين يحملان «سبورت» (نفس مجموعة Flanker) ⇒ لا رفض بسبب الـFlanker."""
    idx = _idx(["عطر اختبار حصري سبورت 100 مل"])
    cands = _search(idx, "عطر اختبار حصري سبورت 100 مل")
    assert len(cands) == 1


def test_must_match_word_rejects_missing_synonym():
    """منتجنا يحمل «كاربون» والمنافس لا يحمل أي مرادف لها ⇒ رفض (حارس الكلمات الإلزامية)."""
    idx = _idx(["عطر اختبار حصري 100 مل"])
    assert _search(idx, "عطر اختبار حصري كاربون 100 مل") == []


def test_must_match_word_accepts_synonym_spelling():
    """المنافس يحمل مرادف «carbon» الإنجليزي ⇒ يُقبل (نفس مجموعة المرادفات)."""
    idx = _idx(["عطر اختبار حصري carbon 100 مل"])
    cands = _search(idx, "عطر اختبار حصري كاربون 100 مل")
    assert len(cands) == 1
