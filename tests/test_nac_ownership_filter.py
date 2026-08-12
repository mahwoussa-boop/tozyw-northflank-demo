"""tests/test_nac_ownership_filter.py — فلتر ملكية «جديد عند المنافسين».

طبقة عرض/تصفية خالصة على feed المحسوب: filter_feed_by_ownership يرشّح حسب
KEY_OWNERSHIP («» = الكل)؛ الافتراضي «missing». صفوف بلا قرار ملكية تظهر في
«الكل» فقط. لا يمسّ بيانات ولا كشط/مطابقة/إرسال.
"""
from services.ownership_service import KEY_OWNERSHIP
from ui.pages.new_at_competitors import (
    _feed_has_ownership,
    _ownership_counts,
    filter_feed_by_ownership,
)


def _feed():
    return [
        {"n": 1, KEY_OWNERSHIP: "missing"},
        {"n": 2, KEY_OWNERSHIP: "owned"},
        {"n": 3, KEY_OWNERSHIP: "review"},
        {"n": 4, KEY_OWNERSHIP: "missing"},
    ]


def test_default_missing_shows_only_missing():
    out = filter_feed_by_ownership(_feed(), "missing")
    assert [r["n"] for r in out] == [1, 4]


def test_all_shows_everything():
    assert len(filter_feed_by_ownership(_feed(), "")) == 4


def test_review_and_owned_filters():
    assert [r["n"] for r in filter_feed_by_ownership(_feed(), "review")] == [3]
    assert [r["n"] for r in filter_feed_by_ownership(_feed(), "owned")] == [2]


def test_counts_per_category():
    assert _ownership_counts(_feed()) == {"missing": 2, "review": 1, "owned": 1}


def test_rows_without_ownership_appear_only_in_all():
    feed = [{"n": 1}, {"n": 2, KEY_OWNERSHIP: "missing"}]
    assert len(filter_feed_by_ownership(feed, "")) == 2           # الكل يشملها
    assert [r["n"] for r in filter_feed_by_ownership(feed, "missing")] == [2]  # بلا مفتاح لا تظهر
    assert _feed_has_ownership([{"n": 1}]) is False               # لا قرارات ⇒ أظهر الكل
    assert _feed_has_ownership(feed) is True


def test_filter_does_not_mutate_feed():
    feed = _feed()
    snapshot = [dict(r) for r in feed]
    filter_feed_by_ownership(feed, "missing")
    assert feed == snapshot
