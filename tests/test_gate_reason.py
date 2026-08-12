"""tests/test_gate_reason.py — يميّز gate_reason الحالات الأربع + حالة «عبر».

gate_reason دالة بيان خالصة تطابق منطق بوابة الجودة (missing_orchestrator
._apply_quality_gate) دون تغيير القبول/الرفض، وتُعيد أوّل شرط فاشل بالترتيب:
سعر ≤ 0 ← لا صورة حقيقية ← وصف قصير/بلا علامة ← لا ماركة.
"""
from services.enrichment_service import (
    GATE_REASON_BRAND,
    GATE_REASON_DESC,
    GATE_REASON_IMAGE,
    GATE_REASON_PRICE,
    gate_reason,
)

# وصف بتنسيق مهووس: طوله ≥ 60 ويحوي علامة معروفة («مهووس»/«الهرم العطري»…).
_GOOD_DESC = (
    "عطر مهووس فاخر — الهرم العطري: مقدمة العطر وقلب العطر وقاعدة العطر "
    "بنفحات راقية تدوم طويلاً على البشرة طوال اليوم."
)
_GOOD_IMG = "https://cdn.example.com/p/123.jpg"


def _row(**overrides):
    """صف مكتمل يعبر البوابة، مع إمكان تجاوز أي مفتاح لاختبار سقوطه."""
    base = {
        "سعر_المنافس": 199,
        "الوصف": _GOOD_DESC,
        "صورة_المنافس": _GOOD_IMG,
        "الماركة": "ديور",
    }
    base.update(overrides)
    return base


def test_complete_row_passes():
    assert gate_reason(_row()) == ""


def test_enriched_flag_passes_even_if_piece_missing():
    # علم الإثراء الصريح يعبر (مع سعر > 0) حتى لو نقص ركن — يطابق is_enriched.
    assert gate_reason(_row(_enriched=True, صورة_المنافس="")) == ""


def test_price_zero_rejected():
    assert gate_reason(_row(**{"سعر_المنافس": 0})) == GATE_REASON_PRICE


def test_price_has_priority_over_other_failures():
    assert (
        gate_reason(_row(**{"سعر_المنافس": 0, "صورة_المنافس": "", "الماركة": ""}))
        == GATE_REASON_PRICE
    )


def test_missing_real_image_rejected():
    assert gate_reason(_row(صورة_المنافس="placeholder.png")) == GATE_REASON_IMAGE


def test_short_or_unmarked_description_rejected():
    assert gate_reason(_row(الوصف="عطر جميل")) == GATE_REASON_DESC


def test_missing_brand_rejected():
    assert gate_reason(_row(الماركة="")) == GATE_REASON_BRAND


def test_gate_reason_is_pure_and_does_not_mutate_row():
    row = _row(الماركة="")
    snapshot = dict(row)
    gate_reason(row)
    assert row == snapshot  # خالص: لا يكتب _gate_reason ولا يغيّر أي مفتاح
