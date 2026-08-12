"""tests/test_opportunity_precompute.py — حارس جدول الفرص المُسبق الحساب.

يختبر ``rebuild_opportunity_scores`` على قاعدة مؤقتة بصفوف معروفة: بناء صحيح،
بناء مرتين بلا تكرار (DELETE+INSERT)، ومصدر معطوب ⇒ 0 بلا استثناء.
"""
import sqlite3

from services.opportunity_service import (
    Opportunity,
    rebuild_opportunity_scores,
    top_opportunities,
)

_CPS = """CREATE TABLE competitor_products_store (
    competitor TEXT, norm_name TEXT, price REAL, availability INTEGER,
    rating_avg REAL, rating_count INTEGER, updated_at TEXT,
    image_url TEXT, size TEXT, product_url TEXT
)"""


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(_CPS)
    conn.execute("CREATE TABLE our_catalog (norm_name TEXT)")
    now = "2026-07-08 12:00:00"
    rows = [
        ("s1", "عطر a", 100.0, 1, 4.5, 10, now),
        ("s2", "عطر a", 110.0, 1, 4.0, 5, now),
        ("s3", "عطر a", 120.0, 0, None, 0, now),
        ("s1", "عطر b", 50.0, 1, 3.0, 2, now),
        ("s2", "عطر b", 55.0, 1, 3.5, 3, now),  # متجران ⇒ يعبر عتبة ≥2
    ]
    conn.executemany(
        "INSERT INTO competitor_products_store "
        "(competitor, norm_name, price, availability, rating_avg, rating_count, updated_at) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO our_catalog VALUES ('عطر b')")
    conn.commit()
    conn.close()


def _rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {r["norm_name"]: dict(r) for r in conn.execute("SELECT * FROM opportunity_scores")}
    finally:
        conn.close()


def test_rebuild_creates_rows(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db)
    n = rebuild_opportunity_scores(db)
    assert n == 2  # مجموعتان تعبران ≥2: عطر a (3 متاجر) + عطر b (2)
    rows = _rows(db)
    assert set(rows) == {"عطر a", "عطر b"}
    assert rows["عطر a"]["store_count"] == 3
    assert rows["عطر b"]["store_count"] == 2
    assert rows["عطر b"]["in_our_catalog"] in (1, True)   # موجود في our_catalog
    assert rows["عطر a"]["in_our_catalog"] in (0, False)  # غير موجود
    assert rows["عطر a"]["rebuilt_at"]                    # طابع زمني موجود
    assert "score" in rows["عطر a"]


def test_rebuild_twice_no_duplication(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db)
    assert rebuild_opportunity_scores(db) == 2
    assert rebuild_opportunity_scores(db) == 2   # DELETE+INSERT — لا تراكم
    conn = sqlite3.connect(db)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM opportunity_scores").fetchone()[0]
    finally:
        conn.close()
    assert cnt == 2


def test_rebuild_broken_source_returns_zero(tmp_path):
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()  # قاعدة بلا competitor_products_store
    assert rebuild_opportunity_scores(db) == 0   # لا استثناء، صفر


def test_top_opportunities_reads_table_ordered(tmp_path):
    """جدول مملوء ⇒ كائنات Opportunity مرتّبة تنازلياً بالنقاط + فلتر min_stores."""
    db = str(tmp_path / "t.db")
    _make_db(db)
    rebuild_opportunity_scores(db)
    opps = top_opportunities(limit=0, min_stores=2, db_path=db)
    assert len(opps) == 2
    assert all(isinstance(o, Opportunity) for o in opps)
    assert [o.norm_name for o in opps] == ["عطر a", "عطر b"]   # a أعلى نقاطاً
    scores = [o.score for o in opps]
    assert scores == sorted(scores, reverse=True)
    # فلتر min_stores يطبَّق وقت القراءة
    assert [o.norm_name for o in top_opportunities(min_stores=3, db_path=db)] == ["عطر a"]


def test_top_opportunities_missing_table_returns_empty(tmp_path):
    """جدول غائب ⇒ [] بلا استثناء (لا مسار حساب حيّ بطيء)."""
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    assert top_opportunities(db_path=db) == []


# ── حقول العرض (صورة/حجم/رابط) — إضافة صرفة، لا تمسّ الدرجة/التسعير ──────────
def _make_db_media(path):
    """قاعدة بحقول الوسائط: «عطر ص» متجرٌ فارغ الصورة وآخر بصورة/حجم/رابط (اختبار
    الممثّل غير الفارغ)؛ «عطر ع» بلا أيّ صورة (اختبار None لا يكسر)."""
    conn = sqlite3.connect(path)
    conn.execute(_CPS)
    conn.execute("CREATE TABLE our_catalog (norm_name TEXT)")
    now = "2026-07-12 12:00:00"
    rows = [  # competitor, norm_name, price, avail, r_avg, r_cnt, updated_at, image_url, size, product_url
        ("s1", "عطر ص", 100.0, 1, 4.0, 5, now, "", "", ""),
        ("s2", "عطر ص", 110.0, 1, 4.0, 5, now, "http://img/x.jpg", "100ml", "http://p/x"),
        ("s1", "عطر ع", 50.0, 1, 3.0, 2, now, None, None, None),
        ("s2", "عطر ع", 55.0, 1, 3.0, 2, now, None, None, None),
    ]
    conn.executemany(
        "INSERT INTO competitor_products_store (competitor, norm_name, price, availability, "
        "rating_avg, rating_count, updated_at, image_url, size, product_url) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_media_fields_representative_and_none(tmp_path):
    """يُختار ممثّل غير فارغ عبر المتاجر (MAX+NULLIF)؛ وغياب الصورة ⇒ None لا كسر."""
    db = str(tmp_path / "m.db")
    _make_db_media(db)
    assert rebuild_opportunity_scores(db) == 2
    by = {o.norm_name: o for o in top_opportunities(limit=0, min_stores=2, db_path=db)}
    assert by["عطر ص"].image_url == "http://img/x.jpg"   # التقط غير الفارغ رغم فراغ متجر
    assert by["عطر ص"].size == "100ml"
    assert by["عطر ص"].product_url == "http://p/x"
    assert by["عطر ع"].image_url is None                  # لا صورة إطلاقاً ⇒ None
    assert by["عطر ع"].size is None
    assert by["عطر ع"].product_url is None


# مخطّط الكاش القديم (16 عموداً، بلا حقول العرض) — لمحاكاة الجدول الحيّ القائم.
_OLD_OPP = (
    "CREATE TABLE opportunity_scores (norm_name, store_count, out_ratio, price_min, "
    "price_median, price_max, instock_price_min, rating_avg, rating_count, in_our_catalog, "
    "suggested_price, score, last_seen, data_age_days, stale_reason, rebuilt_at)"
)


def test_rebuild_upgrades_existing_old_schema(tmp_path):
    """جدول قديم قائم بلا أعمدة العرض ⇒ DROP+CREATE يُرقّيه ذرّياً (تحذير الكاش الحاسم)."""
    db = str(tmp_path / "u.db")
    _make_db_media(db)
    conn = sqlite3.connect(db)
    conn.execute(_OLD_OPP)                                   # كاش قديم قائم
    conn.execute("INSERT INTO opportunity_scores (norm_name) VALUES ('قديم')")
    conn.commit()
    conn.close()
    assert rebuild_opportunity_scores(db) == 2               # يعيد البناء فوق القديم
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        schema = {c[1] for c in conn.execute("PRAGMA table_info(opportunity_scores)")}
        names = {r["norm_name"] for r in conn.execute("SELECT norm_name FROM opportunity_scores")}
    finally:
        conn.close()
    assert {"image_url", "size", "product_url"} <= schema     # الأعمدة الجديدة أُضيفت
    assert names == {"عطر ص", "عطر ع"}                        # الصف الوهمي القديم استُبدل كلياً
