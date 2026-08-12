"""tests/test_deterministic_salvage.py — إنقاذ المستبعدات الحتمي (بلا AI).

يغطّي: مطابقة حقيقية تُنقَذ (بحقول منافس مملوءة) · تطابق جزئي يُرفَض بالمقياس
الصارم · تعارض الجنس يُرفَض · غير العطر/التستر/الطقم لا يُفحَص · حفظ الأعداد.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from conf.constants import (
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_DECISION,
    COL_GENDER,
    COL_MATCH_RATIO,
    COL_OUR_NAME,
    COL_OUR_PRICE,
)
from services.deterministic_salvage import (
    DetSalvageResult,
    apply_deterministic_salvage,
    is_single_perfume,
    salvage_excluded_deterministic,
)

_COL_ALL_COMP = "جميع_المنافسين"


def _excluded(names_prices: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{COL_OUR_NAME: n, COL_OUR_PRICE: p} for n, p in names_prices]
    )


def _comps(items: list[dict]) -> pd.DataFrame:
    base = {"product_name": "", "price": 0.0, "product_url": "",
            "image_url": "", "competitor": "", "size": "", "gender": ""}
    return pd.DataFrame([{**base, **it} for it in items])


# ─── المصنّف ─────────────────────────────────────────────────────────
def test_is_single_perfume_accepts_perfume():
    assert is_single_perfume("عطر ليلة خميس للرجال 100مل") is True


@pytest.mark.parametrize("name", [
    "تستر شانيل بلو 100مل",
    "مجموعة عطور لي ولها للجنسين",
    "عينة دانهيل ايكون 2مل",
    "شامبو صبغة لون بني 420مل",
    "مزيل عرق ريكسونا 50مل",
    "بخور عود كلمات",
])
def test_is_single_perfume_rejects_non_perfume(name):
    assert is_single_perfume(name) is False


# ─── مطابقة حقيقية تُنقَذ ─────────────────────────────────────────────
def test_genuine_match_rescued_to_review():
    exc = _excluded([("عطر ليلة خميس للرجال 100مل", 150.0)])
    comp = _comps([
        {"product_name": "عطر ليلة خميس للرجال او دي برفيوم 100 مل",
         "price": 120.0, "competitor": "متجر س", "product_url": "http://x/1"},
        {"product_name": "عطر شانيل نمبر فايف نسائي 100مل", "price": 500.0},
    ])
    res = salvage_excluded_deterministic(exc, comp)
    assert isinstance(res, DetSalvageResult)
    assert len(res.salvaged_matches) == 1
    assert len(res.stayed_excluded) == 0
    row = res.salvaged_matches.iloc[0]
    assert row[COL_COMP_NAME] == "عطر ليلة خميس للرجال او دي برفيوم 100 مل"
    assert float(row[COL_COMP_PRICE]) == 120.0
    assert int(row[COL_MATCH_RATIO]) >= 90
    assert "مراجعة" in str(row[COL_DECISION])
    # عمود الحَلَبة مملوء بمفاتيح إنجليزية صالحة
    parsed = json.loads(row[_COL_ALL_COMP])
    assert parsed[0]["price"] == 120.0 and parsed[0]["name"]


# ─── لا نظير قريب → يبقى مستبعداً ────────────────────────────────────
def test_no_close_competitor_stays_excluded():
    exc = _excluded([("عطر ليلة خميس للرجال 100مل", 150.0)])
    comp = _comps([{"product_name": "عطر شانيل نمبر فايف نسائي 100مل", "price": 500.0}])
    res = salvage_excluded_deterministic(exc, comp)
    assert len(res.salvaged_matches) == 0
    assert len(res.stayed_excluded) == 1
    assert res.n_perfumes == 1


# ─── تطابق جزئي (token_set عالٍ، token_sort منخفض) يُرفَض ─────────────
def test_partial_subset_rejected_by_token_sort():
    # اسمنا مجموعة فرعية من اسم المنافس المطوّل → token_set عالٍ لكن token_sort منخفض
    exc = _excluded([("نجمة الصباح", 100.0)])
    comp = _comps([{
        "product_name": "نجمة الصباح الذهبية الفاخرة المحدودة الملكية الشرقية النادرة",
        "price": 90.0}])
    res = salvage_excluded_deterministic(exc, comp)
    assert len(res.salvaged_matches) == 0
    assert len(res.stayed_excluded) == 1


# ─── حارس الجنس: اسم متطابق لكن الجنس متعارض → يُرفَض ────────────────
def test_gender_conflict_rejected():
    exc = pd.DataFrame([{
        COL_OUR_NAME: "عطر نور الرجالي 100مل", COL_OUR_PRICE: 100.0, COL_GENDER: "رجالي",
    }])
    comp = _comps([{"product_name": "عطر نور النسائي 100مل", "price": 90.0}])
    res = salvage_excluded_deterministic(exc, comp)
    # بعد تطبيع كلمتي الجنس (ضجيج) يتطابق الاسم؛ الحارس وحده يمنع الإنقاذ
    assert len(res.salvaged_matches) == 0
    assert len(res.stayed_excluded) == 1


# ─── غير العطر لا يُفحَص حتى لو له نظير مطابق ────────────────────────
def test_non_perfume_not_scanned_even_with_match():
    exc = _excluded([("شامبو صبغة لون بني 420مل", 34.0)])
    comp = _comps([{"product_name": "شامبو صبغة لون بني 420مل", "price": 30.0}])
    res = salvage_excluded_deterministic(exc, comp)
    assert res.n_perfumes == 0
    assert len(res.salvaged_matches) == 0
    assert len(res.stayed_excluded) == 1


# ─── حفظ الأعداد: مُنقَذ + باقٍ = الإجمالي (خليط) ────────────────────
def test_conservation_mixed_batch():
    exc = _excluded([
        ("عطر ليلة خميس للرجال 100مل", 150.0),      # يُنقَذ
        ("تستر شانيل بلو 100مل", 200.0),            # تستر — يبقى
        ("مزيل عرق ريكسونا 50مل", 12.0),            # غير عطر — يبقى
        ("عطر مجهول تماماً زابليكوس 100مل", 80.0),  # لا نظير — يبقى
    ])
    comp = _comps([
        {"product_name": "عطر ليلة خميس للرجال او دي برفيوم 100 مل", "price": 120.0},
    ])
    res = salvage_excluded_deterministic(exc, comp)
    assert len(res.salvaged_matches) + len(res.stayed_excluded) == 4
    assert len(res.salvaged_matches) == 1


# ─── مدخلات فارغة / كتالوج فارغ ──────────────────────────────────────
def test_empty_excluded():
    res = salvage_excluded_deterministic(pd.DataFrame(), _comps([]))
    assert len(res.salvaged_matches) == 0 and res.total_rows == 0


def test_empty_competitors_keeps_all():
    exc = _excluded([("عطر ليلة خميس للرجال 100مل", 150.0)])
    res = salvage_excluded_deterministic(exc, pd.DataFrame())
    assert len(res.stayed_excluded) == 1
    assert res.errors  # يُبلّغ عن غياب الكتالوج (لا فقدان صامت)


# ═══════════ النواة المشتركة apply_deterministic_salvage ═══════════
class _FakeResult:
    """بديل خفيف عن AnalysisResult — يحمل section_counts فقط."""
    def __init__(self, counts):
        self.section_counts = counts


def test_apply_moves_sections_and_updates_counts():
    from core.enums import SectionType
    exc = _excluded([
        ("عطر ليلة خميس للرجال 100مل", 150.0),      # يُنقَذ
        ("عطر مجهول تماماً زابليكوس 100مل", 80.0),  # يبقى
    ])
    pool = _comps([
        {"product_name": "عطر ليلة خميس للرجال او دي برفيوم 100 مل", "price": 120.0},
    ])
    sections = {"excluded": exc, "review": pd.DataFrame()}
    result = _FakeResult({SectionType.EXCLUDED: 2, SectionType.REVIEW: 0})

    n = apply_deterministic_salvage(sections, result=result, pool=pool)

    assert n == 1
    assert len(sections["excluded"]) == 1      # نقص واحد
    assert len(sections["review"]) == 1        # زاد واحد
    # العدّاد المخزَّن تتبّع الأقسام الحيّة (لا تجمّد)
    assert result.section_counts[SectionType.EXCLUDED] == 1
    assert result.section_counts[SectionType.REVIEW] == 1


def test_apply_no_match_leaves_everything_and_counts_intact():
    from core.enums import SectionType
    exc = _excluded([("عطر مجهول تماماً زابليكوس 100مل", 80.0)])
    pool = _comps([{"product_name": "عطر شانيل نمبر فايف نسائي", "price": 500.0}])
    sections = {"excluded": exc, "review": pd.DataFrame()}
    result = _FakeResult({SectionType.EXCLUDED: 1, SectionType.REVIEW: 0})

    n = apply_deterministic_salvage(sections, result=result, pool=pool)

    assert n == 0
    assert len(sections["excluded"]) == 1
    assert result.section_counts[SectionType.EXCLUDED] == 1  # بلا تغيير


def test_apply_empty_excluded_is_noop():
    sections = {"excluded": pd.DataFrame(), "review": pd.DataFrame()}
    assert apply_deterministic_salvage(sections, pool=_comps([])) == 0


def test_apply_empty_pool_is_noop():
    exc = _excluded([("عطر ليلة خميس للرجال 100مل", 150.0)])
    sections = {"excluded": exc, "review": pd.DataFrame()}
    # pool فارغ → لا إنقاذ، لا كسر
    assert apply_deterministic_salvage(sections, pool=pd.DataFrame()) == 0
    assert len(sections["excluded"]) == 1
