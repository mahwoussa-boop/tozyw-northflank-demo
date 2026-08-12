# -*- coding: utf-8 -*-
"""اختبارات اكتشاف المتاجر الجديدة (services/store_discovery_service).

يثبّت: (1) يستبعد المعروفة ويرتّب المجهولة بعدد المنتجات، (2) يحترم عتبة
min_products وحدّ top_n، (3) upsert في discovered_stores + قراءة مرتّبة،
(4) القراءة {} آمنة بلا جدول. حقن counts/meta ⇒ بلا شبكة ولا مسّ القاعدة الحيّة.
"""
import sqlite3

from services.store_discovery_service import (
    discover_new_stores,
    discovered_stores_list,
    refresh_discovered_stores,
)

# facet وهمي: {store_id: product_count}
_COUNTS = {100: 5000, 200: 3000, 300: 800, 400: 50, 500: 9000}
_META = {
    200: {"store_name": "متجر ألف", "rating_avg": 4.8, "rating_count": 300},
    300: {"store_name": "متجر باء", "rating_avg": 4.0, "rating_count": 10},
    500: {"store_name": "متجر جيم", "rating_avg": 4.9, "rating_count": 900},
}


def _counts(_q):
    return dict(_COUNTS)


def _meta(sid):
    return _META.get(sid)


def test_excludes_known_and_ranks_by_count():
    # 100 و 400 معروفان؛ 400 أيضاً تحت العتبة
    found = discover_new_stores(
        [100, 400], min_products=500, top_n=10, counts_fn=_counts, meta_fn=_meta,
    )
    ids = [d["store_id"] for d in found]
    assert ids == [500, 200, 300]          # مرتّبة تنازلياً بالعدد، بلا 100/400
    assert found[0]["store_name"] == "متجر جيم"
    assert found[0]["product_count"] == 9000


def test_min_products_threshold():
    found = discover_new_stores(
        [], min_products=1000, top_n=10, counts_fn=_counts, meta_fn=_meta,
    )
    ids = [d["store_id"] for d in found]
    assert ids == [500, 100, 200]          # 300 (800) و 400 (50) مُستبعدان
    assert 300 not in ids


def test_top_n_limit():
    found = discover_new_stores(
        [], min_products=1, top_n=2, counts_fn=_counts, meta_fn=_meta,
    )
    assert len(found) == 2
    assert [d["store_id"] for d in found] == [500, 100]  # الأكبر أولاً


def test_refresh_and_read(tmp_path):
    db = str(tmp_path / "t.db")
    n = refresh_discovered_stores(
        [100], db_path=db, min_products=500, top_n=10,
        counts_fn=_counts, meta_fn=_meta,
    )
    assert n == 3                           # 200, 300, 500 (بلا 100 المعروف)
    lst = discovered_stores_list(db_path=db)
    assert [d["store_id"] for d in lst] == [500, 200, 300]  # مرتّبة بالعدد
    assert lst[0]["store_name"] == "متجر جيم"


def test_refresh_twice_replaces(tmp_path):
    db = str(tmp_path / "t.db")
    refresh_discovered_stores([], db_path=db, min_products=500, counts_fn=_counts, meta_fn=_meta)
    n2 = refresh_discovered_stores([100, 200, 300], db_path=db, min_products=500,
                                   counts_fn=_counts, meta_fn=_meta)
    lst = discovered_stores_list(db_path=db)
    assert n2 == len(lst)                   # DELETE+INSERT — لا تراكم
    assert 100 not in [d["store_id"] for d in lst]


def test_missing_table_safe(tmp_path):
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    assert discovered_stores_list(db_path=db) == []


def test_empty_facet_safe():
    # فشل الشبكة ⇒ facet فارغ ⇒ لا اكتشاف (بلا كسر)
    assert discover_new_stores([1, 2], counts_fn=lambda q: {}, meta_fn=_meta) == []
