# -*- coding: utf-8 -*-
"""اختبارات محرك إعادة التوزيع (services/ai_redistributor).

يثبّت ثلاثة ضمانات حرجة:
  1. حفظ العدد: مجموع كل الأقسام + المفقودات ثابت قبل/بعد أي تطبيق خطة.
  2. بوابة النص البديل: صف منتجنا الذي كتب المحرّك في «منتج_المنافس» علامته
     «❌ لم يتجاوز فلاتر المطابقة» لا يُنقل أبداً إلى «مفقود».
  3. ختم الثقة: أي صف يدخل «مفقود» عبر إعادة التوزيع يحمل «مستوى_الثقة=review»
     (فلا يظهر «مشكوك» في صفحة المفقودات).

كلها على المحرّك النقي بمستودع وهمي (بلا قاعدة بيانات ولا ذكاء اصطناعي حيّ).
"""
import pandas as pd

from services.ai_redistributor import (
    AIRedistributor,
    _is_placeholder_name,
)

_SENTINEL = "❌ لم يتجاوز فلاتر المطابقة / لا يوجد"


class _StubRepo:
    """مستودع قرارات وهمي: يعدّ فقط، لا يكتب قاعدة."""

    def __init__(self):
        self.logged = 0

    def log_decision(self, decision):
        self.logged += 1

    @staticmethod
    def new_batch_id():
        return "test-batch"


def _make_redistributor():
    r = AIRedistributor.__new__(AIRedistributor)
    r._ai = None
    r._repo = _StubRepo()
    r._threshold = 0.85
    r._batch_size = 20
    r._max_per_section = 200
    r._router = None
    return r


def _total(sections, missing_df):
    s = sum(len(v) for v in sections.values())
    m = len(missing_df) if missing_df is not None else 0
    return s + m


# ── 1) بوابة النص البديل ──────────────────────────────────────────────

def test_is_placeholder_name():
    assert _is_placeholder_name(_SENTINEL) is True
    assert _is_placeholder_name("") is True
    assert _is_placeholder_name("   ") is True
    assert _is_placeholder_name("عطر شانيل رقم 5") is False


def test_sentinel_row_not_moved_to_missing():
    """صف منتجنا بعلامة ❌ في «منتج_المنافس» يبقى مستبعداً (لا يذهب لمفقود)."""
    r = _make_redistributor()
    excluded = pd.DataFrame({
        "المنتج": ["عطر روجا دوف فيتيفير 100 مل"],       # منتجنا معبّأ
        "منتج_المنافس": [_SENTINEL],                      # علامة لا-مطابقة
        "نسبة_التطابق": [0.0],
        "معرف_المنتج": ["814318748"],
    })
    sections = {"excluded": excluded}
    plan = r.build_plan(sections, missing_df=None, our_catalog=None)
    to_missing = [m for m in plan.moves if m.new_section == "missing"]
    assert to_missing == [], "النص البديل ❌ يجب ألا يُنقل إلى مفقود"
    assert plan.excluded_locked == 1


def test_real_competitor_low_match_still_moves_to_missing():
    """اسم منافس حقيقي بتطابق ضئيل ⇒ يُنقل لمفقود (السلوك الصحيح محفوظ)."""
    r = _make_redistributor()
    excluded = pd.DataFrame({
        "المنتج": [""],                                   # لا منتج لنا
        "منتج_المنافس": ["عطر نادر لا نملكه 100 مل"],
        "نسبة_التطابق": [5.0],
        "معرف_المنتج": ["999"],
    })
    plan = r.build_plan({"excluded": excluded}, missing_df=None, our_catalog=None)
    to_missing = [m for m in plan.moves if m.new_section == "missing"]
    assert len(to_missing) == 1
    assert to_missing[0].product_name == "عطر نادر لا نملكه 100 مل"


# ── 2) حفظ العدد ──────────────────────────────────────────────────────

def test_conservation_apply_plan():
    """مجموع (الأقسام + المفقودات) ثابت قبل وبعد التطبيق."""
    r = _make_redistributor()
    review = pd.DataFrame({
        "المنتج": ["منتج مطابق أ", "منتج رمادي ب"],
        "منتج_المنافس": ["منافس أ", "منافس ب"],
        "نسبة_التطابق": [92.0, 50.0],          # 92⇒ينتقل، 50⇒يبقى
        "معرف_المنتج": ["1", "2"],
        "السعر": [100.0, 100.0],
        "سعر_المنافس": [90.0, 90.0],
    })
    excluded = pd.DataFrame({
        "المنتج": [""],
        "منتج_المنافس": ["منافس نادر ج"],
        "نسبة_التطابق": [3.0],
        "معرف_المنتج": ["3"],
    })
    sections = {"review": review, "excluded": excluded}
    missing_df = pd.DataFrame({"منتج_المنافس": ["مفقود قديم"], "مستوى_الثقة": ["green"]})

    before = _total(sections, missing_df)
    plan = r.build_plan(sections, missing_df=missing_df, our_catalog=None)
    new_sections, new_missing, report = r.apply_plan(plan, sections, missing_df)
    after = _total(new_sections, new_missing)

    assert before == after, f"فقدان بيانات: قبل={before} بعد={after}"


# ── 3) ختم الثقة ──────────────────────────────────────────────────────

def test_moved_to_missing_gets_review_confidence():
    """صف يدخل «مفقود» من قسم آخر يُختَم «review» (لا يظهر «مشكوك»)."""
    r = _make_redistributor()
    excluded = pd.DataFrame({
        "المنتج": [""],
        "منتج_المنافس": ["عطر منافس حقيقي 100 مل"],
        "نسبة_التطابق": [4.0],
        "معرف_المنتج": ["7"],
    })
    sections = {"excluded": excluded}
    missing_df = pd.DataFrame({"منتج_المنافس": ["مفقود قديم"], "مستوى_الثقة": ["green"]})

    plan = r.build_plan(sections, missing_df=missing_df, our_catalog=None)
    _, new_missing, _ = r.apply_plan(plan, sections, missing_df)

    moved = new_missing[new_missing["منتج_المنافس"] == "عطر منافس حقيقي 100 مل"]
    assert len(moved) == 1
    assert moved.iloc[0]["مستوى_الثقة"] == "review"
    # لا صف بلا مستوى ثقة (لا «مشكوك»)
    levels = new_missing["مستوى_الثقة"].astype(str)
    assert (levels.isin(["green", "review"])).all()
