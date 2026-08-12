# -*- coding: utf-8 -*-
"""اختبارات تقييم المتجر (services/store_profile_service) — وحدة معزولة.

يثبّت: (1) upsert بجلب محقون (بلا شبكة)، (2) القراءة تُرجِع {} بأمان عند غياب
الجدول، (3) الوسم فارغ آمن بلا تقييم، (4) badge_for_name يطابق بالاسم بدقّة.
كلها على SQLite مؤقتة — لا شبكة ولا مسّ القاعدة الحيّة ولا كاشط المنتجات.
"""
import sqlite3

from services.store_profile_service import (
    refresh_store_profiles,
    rising_store_names,
    store_badge_for_name,
    store_profile_map,
    store_rating_badge,
)


class _Comp:
    def __init__(self, name, sid):
        self.name = name
        self.mahally_store_id = sid


def _fake_fetch(mapping):
    def _f(sid):
        return mapping.get(sid)
    return _f


def test_refresh_upserts_profiles(tmp_path):
    db = str(tmp_path / "t.db")
    comps = [_Comp("فانيلا", 111), _Comp("سارا", 222), _Comp("بلا_معرّف", 0)]
    fetch = _fake_fetch({
        111: {"rating_avg": 4.9, "rating_count": 5535, "trust_score": 5.0},
        222: {"rating_avg": 4.5, "rating_count": 100, "trust_score": 4.0},
    })
    n = refresh_store_profiles(comps, fetch_fn=fetch, db_path=db)
    assert n == 2  # الثالث بلا store_id يُتجاوز
    m = store_profile_map(db_path=db)
    assert m["فانيلا"]["rating_count"] == 5535
    assert m["سارا"]["rating_avg"] == 4.5


def test_refresh_upsert_updates_existing(tmp_path):
    db = str(tmp_path / "t.db")
    comps = [_Comp("فانيلا", 111)]
    refresh_store_profiles(comps, _fake_fetch({111: {"rating_avg": 4.0, "rating_count": 10, "trust_score": 3.0}}), db_path=db)
    refresh_store_profiles(comps, _fake_fetch({111: {"rating_avg": 4.9, "rating_count": 5535, "trust_score": 5.0}}), db_path=db)
    m = store_profile_map(db_path=db)
    assert len(m) == 1                       # لا تكرار — upsert
    assert m["فانيلا"]["rating_count"] == 5535  # القيمة محدَّثة


def test_fetch_failure_skips_store(tmp_path):
    db = str(tmp_path / "t.db")
    comps = [_Comp("فانيلا", 111), _Comp("معطوب", 999)]
    fetch = _fake_fetch({111: {"rating_avg": 4.9, "rating_count": 50, "trust_score": 5.0}})  # 999⇒None
    n = refresh_store_profiles(comps, fetch_fn=fetch, db_path=db)
    assert n == 1
    assert "معطوب" not in store_profile_map(db_path=db)


def test_missing_table_safe(tmp_path):
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    assert store_profile_map(db_path=db) == {}


def test_badge_text():
    assert store_rating_badge(
        {"rating_avg": 4.9, "rating_count": 5535}
    ) == "🏪 تقييم المتجر ★4.9 (5,535 تقييم)"
    assert store_rating_badge({"rating_avg": 0, "rating_count": 0}) == ""
    assert store_rating_badge({"rating_avg": 4.5, "rating_count": 0}) == ""
    assert store_rating_badge(None) == ""


def _isolate_caches(monkeypatch, profiles=None, rising=None):
    """يعزل كاشات العملية (لا قراءة من القاعدة الحيّة في الاختبار)."""
    import services.store_profile_service as svc
    monkeypatch.setattr(svc, "_PROC_LOADED", True, raising=False)
    monkeypatch.setattr(svc, "_PROC_CACHE", profiles or {}, raising=False)
    monkeypatch.setattr(svc, "_RISING_LOADED", True, raising=False)
    monkeypatch.setattr(svc, "_RISING_CACHE", set(rising or ()), raising=False)
    return svc


def test_badge_for_name(monkeypatch):
    svc = _isolate_caches(monkeypatch, profiles={
        "فانيلا": {"rating_avg": 4.9, "rating_count": 5535, "trust_score": 5.0},
    })
    assert "★4.9" in svc.store_badge_for_name("فانيلا")
    assert svc.store_badge_for_name("5 متجر") == ""   # اسم غير مطابق
    assert svc.store_badge_for_name("") == ""


def test_rising_symbol_composed(monkeypatch):
    """المتجر الصاعد يُسبَق بـ«📈 صاعد»؛ والصاعد بلا تقييم يعرض الرمز وحده."""
    svc = _isolate_caches(
        monkeypatch,
        profiles={"فانيلا": {"rating_avg": 4.9, "rating_count": 5535, "trust_score": 5.0}},
        rising={"فانيلا", "متجر_صاعد_بلا_تقييم"},
    )
    b = svc.store_badge_for_name("فانيلا")
    assert b.startswith("📈 صاعد")
    assert "★4.9" in b
    # صاعد بلا تقييم ⇒ الرمز فقط
    assert svc.store_badge_for_name("متجر_صاعد_بلا_تقييم") == "📈 صاعد"
    # لا صاعد ولا تقييم ⇒ فارغ
    assert svc.store_badge_for_name("متجر_عادي") == ""


def test_rising_store_names(tmp_path):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE scrape_runs (competitor TEXT, run_at TEXT, new_count INTEGER)")
    conn.executemany(
        "INSERT INTO scrape_runs (competitor, run_at, new_count) VALUES (?, datetime('now'), ?)",
        [("صاعد", 80), ("راكد", 3)],
    )
    # «قديم_كثير» أضاف كثيراً لكن قبل 30 يوماً ⇒ خارج نافذة الـ7 أيام
    conn.execute("INSERT INTO scrape_runs (competitor, run_at, new_count) VALUES ('قديم_كثير', datetime('now','-30 days'), 500)")
    conn.commit(); conn.close()
    r = rising_store_names(days=7, min_new=50, db_path=db)
    assert "صاعد" in r          # 80 ≥ 50 داخل النافذة
    assert "راكد" not in r       # 3 < 50
    assert "قديم_كثير" not in r  # الـ500 خارج نافذة الـ7 أيام


def test_rising_missing_table_safe(tmp_path):
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    assert rising_store_names(db_path=db) == set()
