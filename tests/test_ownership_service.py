"""tests/test_ownership_service.py — طبقة الملكية الموحّدة (جديد/نفذت ↔ كتالوجنا).

النواة تُختبَر بمعزل عبر بديل MatchingService (دون شبكة/قاعدة)، مع اختبار تكامل
خفيف بـ MatchingService حقيقية للتأكد من اكتشاف منتج مطابق تماماً كـ OWNED.
"""
from services.matching_service import MatchingService, MatchOutcome, Ownership
from services.ownership_service import (
    KEY_MATCH_REASON,
    KEY_MATCH_SCORE,
    KEY_OUR_MATCH,
    KEY_OUR_PRODUCT_ID,
    KEY_OWNERSHIP,
    enrich_ownership,
    enrich_row_ownership,
    summarize_ownership,
)


class _FakeMatching:
    """بديل MatchingService: يُرجع نتائج مُحدّدة مسبقاً حسب الاسم."""

    def __init__(self, mapping):
        self._m = mapping

    def evaluate(self, name, brand=""):
        return self._m.get(name, MatchOutcome(Ownership.MISSING, 0.0, None, ""))


def test_owned_row_gets_product_id() -> None:
    matching = _FakeMatching(
        {"عطر A": MatchOutcome(Ownership.OWNED, 95.0, "منتجنا A", "")}
    )
    out = enrich_ownership(
        [{"product_name": "عطر A", "brand": "X", "price": 100}],
        matching, {"منتجنا A": "PID-1"},
    )
    assert out[0][KEY_OWNERSHIP] == "owned"
    assert out[0][KEY_OUR_MATCH] == "منتجنا A"
    assert out[0][KEY_OUR_PRODUCT_ID] == "PID-1"
    assert out[0][KEY_MATCH_SCORE] == 95.0
    assert out[0]["price"] == 100  # الحقول الأصلية محفوظة


def test_missing_row_has_no_product_id() -> None:
    out = enrich_ownership([{"product_name": "عطر غريب"}], _FakeMatching({}), {})
    assert out[0][KEY_OWNERSHIP] == "missing"
    assert out[0][KEY_OUR_MATCH] is None
    assert out[0][KEY_OUR_PRODUCT_ID] == ""


def test_review_row_keeps_reason_and_id() -> None:
    matching = _FakeMatching(
        {"عطر B": MatchOutcome(Ownership.REVIEW, 70.0, "منتجنا B", "بانتظار التحقق")}
    )
    out = enrich_ownership([{"product_name": "عطر B"}], matching, {"منتجنا B": "PID-2"})
    assert out[0][KEY_OWNERSHIP] == "review"
    assert out[0][KEY_OUR_PRODUCT_ID] == "PID-2"
    assert out[0][KEY_MATCH_REASON] == "بانتظار التحقق"


def test_empty_name_is_missing() -> None:
    out = enrich_ownership([{"product_name": ""}, {"price": 5}], _FakeMatching({}), {})
    assert all(r[KEY_OWNERSHIP] == "missing" for r in out)
    assert out[0][KEY_OUR_PRODUCT_ID] == ""


def test_does_not_mutate_input() -> None:
    row = {"product_name": "x"}
    enrich_row_ownership(row, _FakeMatching({}), {})
    assert KEY_OWNERSHIP not in row  # الأصل لم يتغيّر


def test_owned_without_id_in_map_yields_empty_id() -> None:
    matching = _FakeMatching(
        {"عطر C": MatchOutcome(Ownership.OWNED, 90.0, "منتجنا C", "")}
    )
    out = enrich_ownership([{"product_name": "عطر C"}], matching, {})  # لا خريطة
    assert out[0][KEY_OWNERSHIP] == "owned"
    assert out[0][KEY_OUR_PRODUCT_ID] == ""  # لا يُسقِط الصف، فقط بلا معرّف


def test_summarize_counts() -> None:
    rows = [
        {KEY_OWNERSHIP: "owned"}, {KEY_OWNERSHIP: "owned"},
        {KEY_OWNERSHIP: "review"}, {KEY_OWNERSHIP: "missing"},
    ]
    assert summarize_ownership(rows) == {"owned": 2, "review": 1, "missing": 1}


def test_integration_real_matching_detects_owned() -> None:
    """تكامل خفيف: MatchingService حقيقية تكتشف اسماً مطابقاً تماماً كـ OWNED."""
    our = ["عطر لطافة عود مود إليكسير او دو برفيوم 100 مل"]
    matching = MatchingService(our)
    out = enrich_ownership(
        [{"product_name": our[0], "brand": "لطافة"}], matching, {our[0]: "PID-X"},
    )
    assert out[0][KEY_OWNERSHIP] == "owned"
    assert out[0][KEY_OUR_PRODUCT_ID] == "PID-X"


def test_integration_real_matching_unknown_is_missing() -> None:
    """منتج لا صلة له بكتالوجنا ⇒ MISSING."""
    matching = MatchingService(["عطر لطافة عود مود إليكسير او دو برفيوم 100 مل"])
    out = enrich_ownership(
        [{"product_name": "ساعة يد رجالية ستيل فضية", "brand": "كاسيو"}], matching, {},
    )
    assert out[0][KEY_OWNERSHIP] == "missing"


# ── تجميع منتجات المنافسين ─────────────────────────────────────────────────
from services.ownership_service import (  # noqa: E402
    aggregate_competitor_products,
    prepare_competitor_feed,
)

_PROD = "عطر لطافة عود مود إليكسير او دو برفيوم 100 مل"


def test_aggregate_groups_same_product_across_competitors() -> None:
    m = MatchingService([])  # كيرنل التطبيع فقط
    rows = [
        {"product_name": _PROD, "price": 80, "competitor": "متجر أ", "image_url": "a.jpg"},
        {"product_name": _PROD, "price": 70, "competitor": "متجر ب"},
        {"product_name": _PROD, "price": 90, "competitor": "متجر أ"},  # متجر مكرّر
    ]
    out = aggregate_competitor_products(rows, m)
    assert len(out) == 1
    g = out[0]
    assert g["عدد_المنافسين"] == 2                  # أ، ب (بلا تكرار)
    assert g["المنافسون"] == "متجر أ,متجر ب"        # فاصلة ASCII
    assert g["سعر_المنافس"] == 70                   # أرخص سعر
    assert g["صورة_المنافس"] == "a.jpg"             # أول صورة صالحة
    assert g["المنافس"] == "متجر أ"


def test_aggregate_separates_distinct_products() -> None:
    m = MatchingService([])
    rows = [
        {"product_name": _PROD, "price": 80, "competitor": "أ"},
        {"product_name": "ساعة يد رجالية ستيل", "price": 200, "competitor": "ب"},
    ]
    out = aggregate_competitor_products(rows, m)
    assert len(out) == 2


def test_aggregate_skips_empty_name_and_preserves_order() -> None:
    m = MatchingService([])
    rows = [
        {"product_name": "", "price": 5, "competitor": "x"},
        {"product_name": _PROD, "price": 50, "competitor": "أ"},
    ]
    out = aggregate_competitor_products(rows, m)
    assert len(out) == 1
    assert out[0]["منتج_المنافس"] == _PROD


def test_prepare_feed_aggregates_then_enriches_ownership() -> None:
    # كتالوجنا يملك المنتج ⇒ التجميع ثم الإثراء يعطي OWNED + معرّفنا
    m = MatchingService([_PROD])
    rows = [
        {"product_name": _PROD, "price": 80, "competitor": "أ"},
        {"product_name": _PROD, "price": 70, "competitor": "ب"},
    ]
    feed = prepare_competitor_feed(rows, m, {_PROD: "PID-9"})
    assert len(feed) == 1
    assert feed[0][KEY_OWNERSHIP] == "owned"
    assert feed[0][KEY_OUR_PRODUCT_ID] == "PID-9"
    assert feed[0]["عدد_المنافسين"] == 2
