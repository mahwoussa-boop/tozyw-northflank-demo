# -*- coding: utf-8 -*-
"""اختبارات محرك «نملكه باسم آخر» (services/ownership_matcher).

يثبت الدقة: يلتقط نفس المنتج المكتوب باسم مختلف، ويرفض الحجم/النكهة/الجنس/
التركيز المختلف — أي بلا إضاعة فرص. كلها على دوال نقية بلا Streamlit ولا قاعدة."""
import pandas as pd

from services.ownership_matcher import (
    find_owned_matches,
    find_possible_matches,
    owned_comp_names,
    _core_equivalent,
    _gender_compatible,
    _size_compatible,
    _conc_compatible,
)


def _missing(*names):
    return pd.DataFrame({"منتج_المنافس": list(names)})


def test_matches_spelling_variant_same_product():
    """نفس المنتج بإملاء مختلف (او دو/او دي، جولد/قولد) ⇒ يُطابَق."""
    missing = _missing("عطر تروساردي سينت اوف جولد او دو بارفيوم 100مل")
    our = ["عطر تروساردي سينت أوف قولد أو دي بارفيوم 100مل"]
    matches = find_owned_matches(missing, our)
    assert len(matches) == 1
    assert matches[0].comp_name.startswith("عطر تروساردي")


def test_tester_prefix_ignored():
    """«تستر» بادئة ضجيج ⇒ يُطابَق العطر نفسه."""
    missing = _missing("تستر كورلوف إن وايت أو دو تواليت للرجال 88مل")
    our = ["عطر كورلوف إن وايت أو دو تواليت للرجال 88 مل"]
    assert len(find_owned_matches(missing, our)) == 1


def test_different_size_not_matched():
    """30مل ضد 100مل = منتج مختلف ⇒ لا يُطابَق (لا إضاعة فرصة)."""
    missing = _missing("عطر لانكوم تريزور او دو بارفيوم 30مل")
    our = ["عطر لانكوم تريزور او دو بارفيوم 100مل"]
    assert find_owned_matches(missing, our) == []


def test_different_variant_word_not_matched():
    """كول ووتر ضد كول ووتر ويف: كلمة زائدة = منتج مختلف ⇒ لا يُطابَق."""
    missing = _missing("عطر دافيدوف كول ووتر النسائي تواليت 100مل")
    our = ["عطر دافيدوف كول ووتر ويف النسائي او دو تواليت 100مل"]
    assert find_owned_matches(missing, our) == []


def test_different_gender_not_matched():
    """رجالي ضد نسائي بنفس الاسم/الحجم ⇒ لا يُطابَق."""
    missing = _missing("عطر كارولينا هيريرا سي اتش الرجالي او دو بارفيوم 100مل")
    our = ["عطر كارولينا هيريرا سي اتش النسائي او دو بارفيوم 100مل"]
    assert find_owned_matches(missing, our) == []


def test_different_concentration_not_matched():
    """EDP ضد EDT بنفس الاسم/الحجم ⇒ منتج مختلف ⇒ لا يُطابَق."""
    missing = _missing("عطر لاكوست بور هوم الرصاصي او دو بارفيوم 100مل")
    our = ["عطر لاكوست بور هوم الرصاصي او دو تواليت 100مل"]
    assert find_owned_matches(missing, our) == []


def test_empty_inputs_safe():
    """مدخلات فارغة/ناقصة ⇒ [] بلا استثناء."""
    assert find_owned_matches(pd.DataFrame(), ["x"]) == []
    assert find_owned_matches(_missing("عطر ما"), []) == []
    assert find_owned_matches(pd.DataFrame({"عمود_آخر": ["س"]}), ["x"]) == []


def test_owned_comp_names_helper():
    missing = _missing("عطر مايكل كورس النسائي او دو بارفيوم 100مل")
    our = ["عطر مايكل كورس النسائي او دو بارفيوم 100مل"]
    matches = find_owned_matches(missing, our)
    assert owned_comp_names(matches) == {"عطر مايكل كورس النسائي او دو بارفيوم 100مل"}


def test_df_index_preserved():
    """يحافظ على فهرس الصف الأصلي (للإخفاء القابل للتراجع لاحقاً)."""
    missing = pd.DataFrame(
        {"منتج_المنافس": ["منتج غير مطابق", "عطر مايكل كورس النسائي او دو بارفيوم 100مل"]},
        index=[10, 20],
    )
    our = ["عطر مايكل كورس النسائي او دو بارفيوم 100مل"]
    matches = find_owned_matches(missing, our)
    assert len(matches) == 1
    assert matches[0].df_index == 20


def test_core_equivalent_rejects_extra_token():
    assert _core_equivalent(["cool", "water"], ["cool", "water"]) is True
    assert _core_equivalent(["cool", "water"], ["cool", "water", "wave"]) is False


def test_gender_compatible_logic():
    assert _gender_compatible("", "رجالي") is True   # مجهول ⇒ متوافق
    assert _gender_compatible("رجالي", "رجالي") is True
    assert _gender_compatible("رجالي", "نسائي") is False


# ─────────────────────────── طبقة «محتمل» (استرجاع أعلى، مراجعة) ───────────────

def test_possible_matches_unknown_concentration_one_side():
    """تركيز مذكور عندنا (EDP) ومجهول عند المنافس، نفس الاسم/الحجم ⇒ مرشّح محتمل."""
    missing = _missing("عطر بولغاري مان وود ايسنس 60 مل")
    our = ["عطر بولغاري مان وود إيسنس أو دو برفيوم 60 مل"]
    pm = find_possible_matches(missing, our)
    assert len(pm) == 1
    assert pm[0].comp_name.startswith("عطر بولغاري")
    assert "التركيز" in pm[0].reason


def test_possible_rejects_known_different_concentration():
    """EDP ضد EDT (كلاهما معروف) ⇒ ليس محتملاً — لا إضاعة تمييز حقيقي."""
    missing = _missing("عطر لاكوست بور هوم الرصاصي او دو بارفيوم 100مل")
    our = ["عطر لاكوست بور هوم الرصاصي او دو تواليت 100مل"]
    assert find_possible_matches(missing, our) == []


def test_possible_rejects_known_different_size():
    """30مل ضد 100مل (كلاهما معروف) ⇒ ليس محتملاً."""
    missing = _missing("عطر لانكوم تريزور او دو بارفيوم 30مل")
    our = ["عطر لانكوم تريزور او دو بارفيوم 100مل"]
    assert find_possible_matches(missing, our) == []


def test_possible_preserves_flanker_guard():
    """كلمة مميّزة زائدة (ويف) ⇒ ليس محتملاً — لا يعود خطأ النكهات."""
    missing = _missing("عطر دافيدوف كول ووتر النسائي 100مل")   # تركيز مجهول
    our = ["عطر دافيدوف كول ووتر ويف النسائي او دو تواليت 100مل"]
    assert find_possible_matches(missing, our) == []


def test_possible_excludes_strict_matches():
    """ما يلتقطه الصارم (حجم+تركيز معروفان متساويان) لا يتكرّر في المحتمل."""
    missing = _missing("عطر مايكل كورس النسائي او دو بارفيوم 100مل")
    our = ["عطر مايكل كورس النسائي او دو بارفيوم 100مل"]
    assert len(find_owned_matches(missing, our)) == 1
    assert find_possible_matches(missing, our) == []   # لا فجوة ⇒ ليس محتملاً


def test_possible_empty_inputs_safe():
    assert find_possible_matches(pd.DataFrame(), ["x"]) == []
    assert find_possible_matches(_missing("عطر ما"), []) == []
    assert find_possible_matches(pd.DataFrame({"عمود_آخر": ["س"]}), ["x"]) == []


def test_size_conc_compatible_helpers():
    assert _size_compatible(100, 100) is True
    assert _size_compatible(100, 0) is True     # مجهول ⇒ متوافق
    assert _size_compatible(30, 100) is False   # معروفان مختلفان
    assert _conc_compatible("EDP", "EDP") is True
    assert _conc_compatible("EDP", "") is True   # مجهول ⇒ متوافق
    assert _conc_compatible("EDP", "EDT") is False
