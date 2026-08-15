"""حراسة ذاكرة صفحة «جديد عند المنافسين» في حاوية 1 GiB."""
from ui.pages.new_at_competitors import _FEED_ROW_LIMIT


def test_new_competitors_feed_limit_is_bounded_for_small_container():
    """لا يجوز إعادة رفع سقف التغذية إلى 10k بلا قياس ذاكرة صريح."""
    assert 0 < _FEED_ROW_LIMIT <= 1500
