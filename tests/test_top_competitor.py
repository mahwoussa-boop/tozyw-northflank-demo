# -*- coding: utf-8 -*-
"""اختبارات الملف التقييمي لكل منتج + وسم «أكبر منافس» (top_competitor_service).

يثبّت: (1) الإجماليات **لكل منتج** (مجموع التقييمات + المتوسط المرجّح + عدد
المتاجر)، (2) الأقوى = أعلى rating_count (تعادل ⇒ أعلى rating_avg)، (3) منتج بلا
تقييمات لا يدخل الكاش، (4) القراءة تُرجِع {} بأمان عند غياب الجدول، (5) نص الوسم
يقود بإشارة المنتج السوقية. كلها على SQLite مؤقتة (بلا مسّ القاعدة الحيّة).
"""
import sqlite3

from services.top_competitor_service import (
    rebuild_top_competitor,
    top_competitor_badge,
    top_competitor_map,
)


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE competitor_products_store ("
        "norm_name TEXT, competitor TEXT, rating_count INTEGER, rating_avg REAL)"
    )
    conn.executemany(
        "INSERT INTO competitor_products_store (norm_name, competitor, rating_count, rating_avg) "
        "VALUES (?, ?, ?, ?)",
        [
            # منتج أ: متجرين — سوق 105 تقييم، الأقوى «قوي» بـ100
            ("prod_a", "ضعيف", 5, 4.0),
            ("prod_a", "قوي", 100, 4.5),
            # منتج ب: تعادل العدد (10) ⇒ الأعلى متوسطاً يفوز كأقوى
            ("prod_b", "أعلى_متوسط", 10, 4.8),
            ("prod_b", "أدنى_متوسط", 10, 3.0),
            # منتج ج: بلا تقييمات إطلاقاً ⇒ لا يدخل الكاش
            ("prod_c", "بلا", 0, 0.0),
        ],
    )
    conn.commit()
    conn.close()


def test_per_product_aggregates(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    n = rebuild_top_competitor(db)
    assert n == 2  # أ و ب فقط (ج بلا تقييم)
    m = top_competitor_map(db_path=db)

    a = m["prod_a"]
    assert a["total_rating_count"] == 105          # 5 + 100 (لكل المنتج)
    assert a["store_count"] == 2
    # المتوسط المرجّح = (5*4.0 + 100*4.5) / 105 ≈ 4.476
    assert abs(a["market_rating_avg"] - (5 * 4.0 + 100 * 4.5) / 105) < 1e-6
    assert a["competitor"] == "قوي"                # أقوى بائع = أعلى عدد
    assert a["rating_count"] == 100


def test_tiebreak_by_avg(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    rebuild_top_competitor(db)
    b = top_competitor_map(db_path=db)["prod_b"]
    assert b["competitor"] == "أعلى_متوسط"          # تعادل العدد ⇒ الأعلى متوسطاً
    assert b["total_rating_count"] == 20


def test_unrated_product_excluded(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    rebuild_top_competitor(db)
    assert "prod_c" not in top_competitor_map(db_path=db)


def test_scope_filter(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    rebuild_top_competitor(db)
    m = top_competitor_map(norm_names=["prod_a"], db_path=db)
    assert set(m.keys()) == {"prod_a"}


def test_missing_table_safe(tmp_path):
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    assert top_competitor_map(db_path=db) == {}


def test_rebuild_twice_swaps_schema_cleanly(tmp_path):
    """إعادة البناء مرّتين لا تكسر (DROP+CREATE يبدّل المخطّط بأمان)."""
    db = str(tmp_path / "t.db")
    _seed(db)
    assert rebuild_top_competitor(db) == 2
    assert rebuild_top_competitor(db) == 2   # لا خطأ «العمود موجود» ولا تكرار


def test_badge_for_name(tmp_path, monkeypatch):
    """badge_for_name يُطبّع الاسم ويبحث بالكاش؛ فارغ آمن للمجهول."""
    import services.top_competitor_service as svc
    from engines.mahally_scraper import MahallyScraper
    name = "عطر تجريبي للاختبار 100مل"
    key = MahallyScraper.normalize(name)
    # نحقن كاش العملية مباشرة (بلا قاعدة)
    monkeypatch.setattr(svc, "_PROC_LOADED", True, raising=False)
    monkeypatch.setattr(svc, "_PROC_CACHE", {
        key: {"total_rating_count": 200, "market_rating_avg": 4.5,
              "competitor": "متجرX", "rating_count": 180},
    }, raising=False)
    assert "200 تقييم بالسوق" in svc.badge_for_name(name)
    assert svc.badge_for_name("منتج مجهول تماماً") == ""
    assert svc.badge_for_name("") == ""


def test_badge_for_name_empty_cache_safe(monkeypatch):
    import services.top_competitor_service as svc
    monkeypatch.setattr(svc, "_PROC_LOADED", True, raising=False)
    monkeypatch.setattr(svc, "_PROC_CACHE", {}, raising=False)
    assert svc.badge_for_name("أي اسم") == ""


def test_badge_text():
    # يقود بإشارة المنتج السوقية ثم الأقوى
    assert top_competitor_badge({
        "total_rating_count": 629, "market_rating_avg": 4.47,
        "competitor": "سارا ميكب", "rating_count": 626,
    }) == "⭐ 629 تقييم بالسوق · ★4.5 · الأقوى: سارا ميكب (626)"
    # بلا متوسط ⇒ بلا نجمة؛ بلا أقوى ⇒ الإجمالي فقط
    assert top_competitor_badge({
        "total_rating_count": 12, "market_rating_avg": 0.0,
        "competitor": "", "rating_count": 0,
    }) == "⭐ 12 تقييم بالسوق"
    # فارغ / بلا تقييمات ⇒ لا وسم
    assert top_competitor_badge(None) == ""
    assert top_competitor_badge({"total_rating_count": 0}) == ""


def test_rebuild_rollback_preserves_old_cache_on_insert_failure(tmp_path, monkeypatch):
    """ذرّية التبديل: فشل الإدراج ⇒ يبقى الكاش القديم سليماً (تراجع كامل).

    يثبت أن DROP+CREATE داخل المعاملة الصريحة (BEGIN) يتراجعان مع فشل الإدراج، فلا
    يُخلَّف جدولٌ جديد فارغ محلّ الكاش القديم — عطل py3.14 حيث لا تنضمّ DDL للمعاملة
    ضمنياً (تُودَع فوراً) دون BEGIN صريح، فيضيع الكاش القديم عند أي فشل بعدها.
    """
    import services.top_competitor_service as svc

    db = str(tmp_path / "t.db")
    _seed(db)                                 # المصدر + بناء أول ناجح (كاش قديم معروف)
    assert rebuild_top_competitor(db) == 2

    _read = ("SELECT norm_name, competitor FROM product_top_competitor "
             "ORDER BY norm_name")
    conn0 = sqlite3.connect(db)
    old = conn0.execute(_read).fetchall()
    conn0.close()
    assert len(old) == 2                       # كاش قديم بصفّين نتحقّق من بقائه

    # اتصال يفشل عمداً عند الإدراج وحده (بعد DROP+CREATE) — يحاكي فشلاً منتصف التبديل.
    _real_connect = sqlite3.connect

    class _FailInsertConn(sqlite3.Connection):
        def executemany(self, sql, *args, **kwargs):
            if sql.lstrip().upper().startswith("INSERT"):
                raise sqlite3.OperationalError("فشل إدراج مُتعمَّد للاختبار")
            return super().executemany(sql, *args, **kwargs)

    monkeypatch.setattr(
        svc.sqlite3, "connect",
        lambda *a, **k: _real_connect(*a, factory=_FailInsertConn, **k),
    )
    assert rebuild_top_competitor(db) == 0     # الفشل يُبتلَع ⇒ 0 (لا استثناء يتسرّب)
    monkeypatch.undo()                         # استعادة connect الحقيقي قبل القراءة

    conn1 = sqlite3.connect(db)
    after = conn1.execute(_read).fetchall()
    conn1.close()
    assert after == old                        # الكاش القديم سليم (تراجعت المعاملة كاملةً)
