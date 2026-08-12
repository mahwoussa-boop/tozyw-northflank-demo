"""tests/test_fingerprint.py — بصمة هوية المنتج عبر المتاجر + is_duplicate.

تغطّي الثغرة الجوهرية: SKU المنافس ≠ SKU متجرك، والتطبيع الهشّ يجعل نفس
المنتج المكتوب بصيغتين يُعطي مفتاحين مختلفين فلا يُكتشف التكرار. البصمة
هنا تُطبّع التركيز/الوحدات/الرسم فتجمع الصيغ المتعددة في مفتاح واحد، وتفرّق
بين الأحجام والتركيزات (قاعدة العطور: 50 ≠ 100، EDP ≠ EDT).
"""
import pytest

from utils.product_key import fingerprint, fingerprint_key
from engines.duplicate_detector import (
    CatalogIndex,
    DupResult,
    LIKELY,
    MATCH,
    NEW,
    is_duplicate,
)


# ════════════════════════════════════════════════════════════════════
#  البصمة — تجميع الصيغ المتعددة لنفس المنتج
# ════════════════════════════════════════════════════════════════════
def test_fingerprint_collapses_concentration_and_unit_spelling() -> None:
    """نفس المنتج بصيغ تركيز/وحدة مختلفة ⇒ بصمة واحدة (لا تكرار)."""
    a = fingerprint_key("ديور سوفاج او دو بارفيوم 100 مل")
    b = fingerprint_key("ديور سوفاج EDP 100ml")
    c = fingerprint_key("عطر ديور سوفاج 100مل او دو بارفيوم")
    assert a == b == c
    # التركيز مُطبَّع إلى رمز موحّد، والحجم رقم صحيح بالمل
    assert "100ml" in a and "edp" in a


def test_fingerprint_differs_by_size() -> None:
    """اختلاف الحجم ⇒ بصمة مختلفة (منتج مختلف)."""
    assert fingerprint_key("Dior Sauvage EDP 50ml") != \
        fingerprint_key("Dior Sauvage EDP 100ml")


def test_fingerprint_differs_by_concentration() -> None:
    """EDP ≠ EDT لنفس الاسم/الحجم (قاعدة SKU: التركيز يُغيّر الهوية)."""
    edp = fingerprint("شانيل بلو 100مل او دو بارفيوم")
    edt = fingerprint("شانيل بلو 100مل او دو تواليت")
    assert edp.concentration == "edp"
    assert edt.concentration == "edt"
    assert edp.key != edt.key


def test_fingerprint_intense_is_distinct() -> None:
    """النسخة المكثّفة (Intense) منتج مختلف عن العادية."""
    base = fingerprint("Sauvage EDP 100ml").concentration
    intense = fingerprint("Sauvage EDP Intense 100ml").concentration
    assert base == "edp"
    assert "int" in intense and intense != base


def test_fingerprint_extracts_fields() -> None:
    fp = fingerprint("عطر لطافة عود مود 100مل او دو بارفيوم")
    assert fp.size_ml == 100
    assert fp.concentration == "edp"
    assert fp.core  # نواة اسم غير فارغة
    assert fp.is_identifiable


def test_fingerprint_explicit_fields_win() -> None:
    """الحقول الصريحة (حجم/تركيز/باركود/sku) تُفضَّل على المشتقّ من الاسم."""
    fp = fingerprint("منتج بلا حجم", brand="Ajmal", size="75",
                     concentration="EDT", barcode="6291100130474", sku="mh-001")
    assert fp.size_ml == 75
    assert fp.concentration == "edt"
    assert fp.barcode == "6291100130474"
    assert fp.sku == "MH-001"


def test_fingerprint_short_barcode_ignored() -> None:
    """باركود قصير (<6 خانات) لا يُعتمد مفتاحاً قاطعاً."""
    assert fingerprint("x", barcode="123").barcode == ""


# ════════════════════════════════════════════════════════════════════
#  is_duplicate — عقد القرار: match | likely | new
# ════════════════════════════════════════════════════════════════════
@pytest.fixture()
def index() -> CatalogIndex:
    idx = CatalogIndex()
    idx.add("ديور سوفاج او دو بارفيوم 100مل", brand="Dior",
            barcode="3348901250197", sku="DIOR-SAUV-100", ref="P-1")
    idx.add("شانيل بلو دو شانيل 100مل او دو بارفيوم", brand="Chanel", ref="P-2")
    idx.add("لطافة اخلاص 100مل", brand="Lattafa", ref="P-3")
    return idx


def test_barcode_is_decisive(index: CatalogIndex) -> None:
    res = is_duplicate({"name": "منتج باسم مختلف تماماً", "barcode": "3348901250197"}, index)
    assert res.decision == MATCH
    assert res.layer == "barcode"
    assert res.matched == "P-1"


def test_sku_is_decisive(index: CatalogIndex) -> None:
    res = is_duplicate({"name": "اسم آخر", "رمز المنتج sku": "dior-sauv-100"}, index)
    assert res.decision == MATCH
    assert res.layer == "sku"
    assert res.matched == "P-1"


def test_exact_fingerprint_match(index: CatalogIndex) -> None:
    """نفس المنتج بصيغة كتابة مختلفة ⇒ match عبر طبقة البصمة التامة."""
    res = is_duplicate({"name": "ديور سوفاج EDP 100ml", "الماركة": "Dior"}, index,
                       use_fuzzy=False)
    assert res.decision == MATCH
    assert res.layer == "fingerprint"
    assert res.matched == "P-1"


def test_genuinely_new_product(index: CatalogIndex) -> None:
    res = is_duplicate({"name": "نيشان هاسيت بلوش 50مل", "الماركة": "Nishane"}, index)
    assert res.decision == NEW
    assert res.fingerprint  # البصمة محسوبة حتى للجديد (للتسجيل لاحقاً)


def test_string_candidate_supported(index: CatalogIndex) -> None:
    res = is_duplicate("منتج جديد غير موجود إطلاقاً 30مل", index)
    assert res.decision in (NEW, LIKELY)


def test_empty_index_returns_new() -> None:
    res = is_duplicate({"name": "أي منتج 100مل"}, CatalogIndex())
    assert res.decision == NEW


def test_index_build_from_records() -> None:
    idx = CatalogIndex.build([
        {"أسم المنتج": "ديور سوفاج او دو بارفيوم 100مل", "الماركة": "Dior", "product_id": "9"},
        {"أسم المنتج": "شانيل بلو 100مل", "الماركة": "Chanel"},
    ])
    assert len(idx) == 2
    res = is_duplicate({"name": "ديور سوفاج EDP 100ml"}, idx, use_fuzzy=False)
    assert res.decision == MATCH
    assert res.matched == "9"


def test_missing_concentration_falls_back_to_fuzzy() -> None:
    """كتالوج بلا كلمة تركيز: الطبقة التامة لا تحسم، فيلتقطها الحارس الضبابي.

    التركيز جزء من المفتاح التام (قاعدة العطور)، لكن أسماء الكتالوج كثيراً ما
    تُغفله. عندها يتدرّج القرار إلى MatchingService (يُسقط التركيز ويتسامح
    مع الحجم) فلا يضيع التكرار.
    """
    idx = CatalogIndex()
    idx.add("ديور سوفاج 100مل", brand="Dior", ref="P-9")  # بلا تركيز
    candidate = {"name": "ديور سوفاج EDP 100ml", "الماركة": "Dior"}
    # الطبقة التامة وحدها ⇒ جديد (المفتاحان يختلفان في التركيز)
    assert is_duplicate(candidate, idx, use_fuzzy=False).decision == NEW
    # مع الحارس الضبابي ⇒ تكرار مؤكّد
    assert is_duplicate(candidate, idx, use_fuzzy=True).decision == MATCH
