"""حراسة ذاكرة صفحة «جديد عند المنافسين» في حاوية محدودة الذاكرة.

سقف العرض يحمي الواجهة، أما العدّاد فيجب أن يبقى صادقًا عبر COUNT(*)
من دون تحميل آلاف الصفوف إلى ذاكرة عملية Streamlit.
"""
import sqlite3

from services.scraper_service import ScraperService
from ui.pages.new_at_competitors import _FEED_ROW_LIMIT


def _db_with_new_products(path, rows: int) -> None:
    """ينشئ قاعدة منافسين صغيرة تحوي عددًا معروفًا من المنتجات الجديدة."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        " competitor TEXT, product_name TEXT, price REAL, image_url TEXT,"
        " product_url TEXT, brand TEXT, first_seen_at TEXT, availability INTEGER)"
    )
    con.executemany(
        "INSERT INTO competitor_products_store VALUES (?,?,?,?,?,?,datetime('now','localtime'),1)",
        [(f"متجر{i % 3}", f"منتج{i}", 100.0 + i, "", "", "ماركة") for i in range(rows)],
    )
    con.commit()
    con.close()


def test_new_competitors_feed_limit_is_bounded_for_small_container():
    """لا يجوز إعادة رفع سقف التغذية بلا قياس ذاكرة صريح."""
    assert 0 < _FEED_ROW_LIMIT <= 1500


def test_new_products_counter_stays_truthful_above_feed_cap(tmp_path):
    """العدّاد يعد جميع الصفوف وواجهة العرض تبقى مقيدة بالسقف."""
    total = _FEED_ROW_LIMIT + 120
    db = tmp_path / "competitors.db"
    _db_with_new_products(db, total)
    service = ScraperService(links_file=tmp_path / "links.json", competitor_db=db)

    assert service.new_products_count(since_days=1) == total
    assert len(service.new_products(since_days=1, limit=_FEED_ROW_LIMIT)) == _FEED_ROW_LIMIT


def test_new_products_counter_and_list_share_one_definition(tmp_path):
    """فلتر المتجر لا يفصل تعريف العدّاد عن تعريف القائمة."""
    db = tmp_path / "competitors.db"
    _db_with_new_products(db, 40)
    service = ScraperService(links_file=tmp_path / "links.json", competitor_db=db)

    for competitor in (None, "متجر0", "متجر1"):
        counted = service.new_products_count(competitor=competitor, since_days=1)
        listed = len(service.new_products(competitor=competitor, since_days=1, limit=10_000))
        assert counted == listed, f"العدّاد والقائمة اختلفا على «{competitor}»"
