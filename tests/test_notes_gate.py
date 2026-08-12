"""tests/test_notes_gate.py — حارس جودة النوتات في البوابة الحقيقية.

يضمن سلامة بيانات متجر العطور: لا يُنشَر عطرٌ بنوتات «غير متوفر» أو غير
مؤرَّضة (احتياطي AI ⇒ تلفيق محتمل) صامتاً — يُعلَّم للمراجعة. وغير العطور
(عناية/مكياج) لا تُفحَص. آمن ضد الإيجابيات الكاذبة (fail-open بلا حقول نوتات).
"""
from utils.product_gate import assess_notes_quality, validate_and_enrich

# وصف مهووس صالح + صورة حقيقية — يعبران البوابة، فيُعزَل أثر النوتات وحده.
_DESC = ("<div dir='rtl'>وصف مهووس الاحترافي — رحلة العطر عبر الهرم العطري "
         "الفاخر مع تفاصيل المنتج الكاملة وضمان الأصالة 100%.</div>")
_IMG = "https://cdn.salla.sa/stores/mahwous/test-product.jpg"


def _perfume(**extra):
    base = {
        "أسم المنتج": "عطر اختبار فاخر 100مل",
        "الوصف": _DESC, "صورة المنتج": _IMG,
        "اسم التصنيف": "عطور رجالية",
    }
    base.update(extra)
    return base


# ── assess_notes_quality (وحدة) ───────────────────────────────────────
def test_perfume_with_real_notes_ok() -> None:
    review, _ = assess_notes_quality(_perfume(
        top_notes="ليمون، برغموت", heart_notes="ياسمين", base_notes="عنبر، مسك"))
    assert review is False


def test_perfume_all_notes_unavailable_flags_review() -> None:
    review, reason = assess_notes_quality(_perfume(
        top_notes="غير متوفر", heart_notes="غير متوفر", base_notes="غير متوفر"))
    assert review is True and "مراجعة" in reason


def test_perfume_ungrounded_notes_flags_review() -> None:
    # نوتات تبدو حقيقية لكنها من احتياطي AI بلا تأريض ⇒ مراجعة (تلفيق محتمل)
    review, reason = assess_notes_quality(_perfume(
        top_notes="ورد", base_notes="صندل", _notes_grounded=False))
    assert review is True and "مراجعة" in reason


def test_perfume_grounded_notes_ok() -> None:
    review, _ = assess_notes_quality(_perfume(
        top_notes="ورد", base_notes="صندل", _notes_grounded=True))
    assert review is False


def test_perfume_without_note_fields_is_fail_open() -> None:
    # لا حقول نوتات إطلاقاً ⇒ قد تكون ضمن الوصف ⇒ لا نحجب (تفادي إيجابية كاذبة)
    review, _ = assess_notes_quality(_perfume())
    assert review is False


def test_non_perfume_not_checked() -> None:
    # منتج عناية بنوتات «غير متوفر» ⇒ لا يُفحَص (ليس عطراً)
    care = _perfume(top_notes="غير متوفر", heart_notes="غير متوفر",
                    base_notes="غير متوفر")
    care["اسم التصنيف"] = "العناية"
    review, _ = assess_notes_quality(care)
    assert review is False


# ── البوابة الحقيقية validate_and_enrich (تكامل) ──────────────────────
def test_gate_rejects_perfume_with_missing_notes() -> None:
    valid, rejected = validate_and_enrich(
        [_perfume(top_notes="غير متوفر", heart_notes="غير متوفر",
                  base_notes="غير متوفر")],
        auto_generate_desc=False)
    assert len(valid) == 0 and len(rejected) == 1
    assert "مراجعة" in rejected[0]["reason"]


def test_gate_passes_perfume_with_real_notes() -> None:
    valid, rejected = validate_and_enrich(
        [_perfume(top_notes="ليمون", heart_notes="ياسمين", base_notes="مسك",
                  _notes_grounded=True)],
        auto_generate_desc=False)
    assert len(valid) == 1 and len(rejected) == 0


def test_gate_passes_perfume_without_note_fields() -> None:
    # fail-open: بلا حقول نوتات لا يُحجَب (وصف+صورة كافيان للبوابة القديمة)
    valid, rejected = validate_and_enrich([_perfume()], auto_generate_desc=False)
    assert len(valid) == 1 and len(rejected) == 0
