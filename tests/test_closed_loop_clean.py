"""tests/test_closed_loop_clean.py — اختبارات التنظيف العميق ومصفوفة القرار
(الدائرة المقفلة v2.0 — الخطوتان 1 و 2).

أهمّ اختبار (الأمان): ``deep_clean`` لا يُرجع نصاً فارغاً ولا يُشوّه اسماً قصيراً
حتى لو كان كلّه كلمات تسويقية مثل «تستر أصلي» — حماية الضجيج (Guarded Noise).
"""
import pytest

from engines.closed_loop_engine import (
    STATUS_EXCLUDED,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_REVIEW,
    StructMatch,
    _build_struct_match,
    decide,
    deep_clean,
    fuse_scores,
)


# ── 1) الأمان: لا تفريغ للأسماء التي كلّها ضجيج ──────────────────────────
@pytest.mark.parametrize(
    "name", ["تستر أصلي", "Tester Original", "أصلي جديد عرض", "NEW SALE"]
)
def test_deep_clean_never_empties_all_noise(name: str) -> None:
    out = deep_clean(name)
    assert out.strip(), f"deep_clean أفرغ اسماً كلّه ضجيج: {name!r} → {out!r}"


# ── 2) إزالة الضجيج مع بقاء هوية المنتج ──────────────────────────────────
def test_deep_clean_strips_noise_keeps_product() -> None:
    out = deep_clean("Dior Sauvage EDP 100ml tester")
    assert "dior" in out and "sauvage" in out      # هوية المنتج محفوظة
    assert "tester" not in out                       # الضجيج أُزيل
    assert "edp" in out and "100" in out             # النوع والحجم محفوظان


# ── 3) لا تشويه للأسماء القصيرة الحقيقية ─────────────────────────────────
def test_deep_clean_preserves_short_real_name() -> None:
    out = deep_clean("Chanel No 5")
    assert "chanel" in out and "5" in out


# ── 4) تطبيع الماركة عربي→إنجليزي حتى بعد الطيّ (إصلاح المفاتيح المطويّة) ──
@pytest.mark.parametrize(
    "raw,brand",
    [
        ("عطر أرماني للرجال", "armani"),
        ("أمواج انترلود", "amouage"),   # كان يفشل في normalize_text قبل الطيّ
        ("عطر ديور سوفاج", "dior"),
    ],
)
def test_deep_clean_brand_normalization(raw: str, brand: str) -> None:
    assert brand in deep_clean(raw)


# ── 5) تطبيع النوع + توحيد الأرقام العربية ───────────────────────────────
def test_deep_clean_type_and_arabic_digits() -> None:
    assert "edp" in deep_clean("او دو بارفان")
    out = deep_clean("عطر ١٠٠ مل")
    assert "100" in out and "ml" in out


# ── 6) الاستقرار + الكاش لا يُفسد النتيجة ─────────────────────────────────
def test_deep_clean_stable_and_cached() -> None:
    assert deep_clean("Dior Sauvage") == deep_clean("Dior Sauvage")


# ══════════════════════════════════════════════════════════════════════════
#  مصفوفة القرار — decide
# ══════════════════════════════════════════════════════════════════════════
_AGREE = StructMatch(brand="agree", size="agree", type="agree")
_PARTIAL = StructMatch(brand="agree")                 # إشارة جزئية، بلا تعارض
_NONE = StructMatch()                                 # كلّها unknown
_CONFLICT = StructMatch(brand="conflict", size="agree", type="agree")


def test_decide_matrix_covers_all_branches() -> None:
    assert decide(85, _NONE) == STATUS_MATCHED        # ≥80 بلا تعارض
    assert decide(75, _AGREE) == STATUS_MATCHED        # 70-80 + اتفاق كامل
    assert decide(75, _PARTIAL) == STATUS_REVIEW       # 70-80 بلا اتفاق كامل
    assert decide(65, _NONE) == STATUS_REVIEW          # 60-80 رمادي
    assert decide(50, _PARTIAL) == STATUS_REVIEW       # <60 لكن إشارة (درس Stage B)
    assert decide(50, _NONE) == STATUS_MISSING         # <60 بلا إشارة
    assert decide(99, _CONFLICT) == STATUS_EXCLUDED    # التعارض يتغلّب على الدرجة


def test_fuse_scores_blends_in_range() -> None:
    # token_set عالٍ يهيمن؛ partial ضعيف لا يُسقطه دون عتبة المراجعة.
    assert fuse_scores(100, 90, 70) >= 90
    assert 0 <= fuse_scores(0, 0, 0) <= 100
    assert 0 <= fuse_scores(100, 100, 100) <= 100


# ══════════════════════════════════════════════════════════════════════════
#  _build_struct_match — بناء كائن StructMatch من الفحص الهيكلي
# ══════════════════════════════════════════════════════════════════════════

def test_build_struct_match_agree_all() -> None:
    """ماركة+حجم+نوع متطابقان → agree في الأبعاد الثلاثة."""
    s = _build_struct_match(
        "dior sauvage edp 100ml",
        "dior sauvage edp 100ml",
        ["dior"],
    )
    assert s.brand == "agree"
    assert s.size == "agree"
    assert s.type == "agree"
    assert s.full_agree is True


def test_build_struct_match_brand_conflict() -> None:
    """ماركتان مختلفتان → conflict في الماركة."""
    s = _build_struct_match(
        "dior sauvage edp 100ml",
        "chanel bleu edp 100ml",
        ["dior", "chanel"],
    )
    assert s.brand == "conflict"
    assert s.has_conflict is True


def test_build_struct_match_unknown_when_missing() -> None:
    """بُعد غائب من أحد الجانبين → unknown (لا يُعدّ تعارضاً)."""
    s = _build_struct_match(
        "عطر رجالي 100ml",
        "perfume 100ml",
        [],
    )
    assert s.brand == "unknown"
    assert s.size == "agree"       # الحجم متوفر في كليهما
    assert s.type == "unknown"     # النوع غائب


# ══════════════════════════════════════════════════════════════════════════
#  إصلاح التسريب: الطبقة 3 — منتجات score<65% مع إشارة هيكلية → REVIEW
# ══════════════════════════════════════════════════════════════════════════

def test_decide_low_score_with_signal_is_review() -> None:
    """score=55% مع إشارة ماركة → مراجعة (ليس مفقوداً)."""
    brand_signal = StructMatch(brand="agree")
    assert decide(55, brand_signal) == STATUS_REVIEW


def test_decide_low_score_with_size_signal_is_review() -> None:
    """score=40% مع إشارة حجم → مراجعة (ليس مفقوداً)."""
    size_signal = StructMatch(size="agree")
    assert decide(40, size_signal) == STATUS_REVIEW


def test_decide_low_score_no_signal_is_missing() -> None:
    """score=30% بلا أي إشارة → مفقود مؤكد."""
    assert decide(30, _NONE) == STATUS_MISSING


def test_decide_low_score_with_conflict_is_excluded() -> None:
    """score=45% مع تعارض → مستبعد (التعارض يتغلّب)."""
    conflict = StructMatch(brand="conflict")
    assert decide(45, conflict) == STATUS_EXCLUDED
