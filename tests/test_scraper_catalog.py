"""tests/test_scraper_catalog.py — اختبارات تحميل الكتالوج وإدارة المنافسين."""
import pandas as pd
import pytest

from conf.constants import COL_OUR_ID, COL_OUR_NAME, COL_OUR_PRICE
from services.catalog_service import load_catalog_bytes, map_columns, name_column
from services.scraper_service import Competitor, ScraperService, extract_store_id

# CSV بصيغة سلة: صف meta «بيانات المنتج» ثم الترويسة الحقيقية ثم المنتجات.
_SALLA_CSV = (
    "بيانات المنتج,,,\n"
    "No.,أسم المنتج,سعر المنتج,الماركة\n"
    "1,عطر شانيل شانس 100 مل,250,Chanel\n"
    "2,عطر ديور سوفاج 100 مل,300,Dior\n"
).encode("utf-8")

_PLAIN_CSV = "معرف_المنتج,المنتج,السعر\nA,عطر,100\n".encode("utf-8")


def test_load_salla_skips_meta_and_maps_columns() -> None:
    df = load_catalog_bytes(_SALLA_CSV, "store.csv")
    assert len(df) == 2
    assert COL_OUR_NAME in df.columns and COL_OUR_PRICE in df.columns
    assert COL_OUR_ID in df.columns
    assert df.iloc[0][COL_OUR_NAME] == "عطر شانيل شانس 100 مل"


def test_load_plain_csv_without_meta() -> None:
    df = load_catalog_bytes(_PLAIN_CSV, "plain.csv")
    assert len(df) == 1 and df.iloc[0][COL_OUR_NAME] == "عطر"


def test_map_columns_and_name_detection() -> None:
    raw = pd.DataFrame({"أسم المنتج": ["x"], "سعر المنتج": [9]})
    mapped = map_columns(raw)
    assert COL_OUR_NAME in mapped.columns
    assert name_column(mapped) == COL_OUR_NAME
    # استدلال حين غياب العمود الداخلي
    assert name_column(pd.DataFrame({"product name": ["x"]})) == "product name"


def test_extract_store_id() -> None:
    assert extract_store_id("https://mahally.com/stores/216339537/") == 216339537
    assert extract_store_id("https://mahally.com/stores/42") == 42
    assert extract_store_id("https://example.com/no-id") is None
    assert extract_store_id("") is None


def test_scraper_add_list_remove(tmp_path) -> None:
    svc = ScraperService(links_file=tmp_path / "comps.json")
    assert svc.list_competitors() == []
    comp = svc.add_competitor("متجر العطور", "https://mahally.com/stores/100/")
    assert isinstance(comp, Competitor) and comp.mahally_store_id == 100
    listed = svc.list_competitors()
    assert len(listed) == 1 and listed[0].name == "متجر العطور"
    assert svc.remove_competitor(100) is True
    assert svc.list_competitors() == []
    assert svc.remove_competitor(999) is False  # غير موجود


def test_scraper_add_validations(tmp_path) -> None:
    svc = ScraperService(links_file=tmp_path / "c.json")
    with pytest.raises(Exception):
        svc.add_competitor("x", "https://bad-url.com")        # لا معرّف
    svc.add_competitor("A", "https://mahally.com/stores/5/")
    with pytest.raises(Exception):
        svc.add_competitor("A2", "https://mahally.com/stores/5/")  # تكرار


def test_new_oos_and_last_scrape_run(tmp_path) -> None:
    """new_products / out_of_stock / last_scrape_run على قاعدة منافسين مؤقتة."""
    import sqlite3
    db = tmp_path / "comp.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, competitor TEXT, product_name TEXT, price REAL, "
        "image_url TEXT, product_url TEXT, brand TEXT, "
        "first_seen_at TEXT, updated_at TEXT, availability INTEGER DEFAULT 1)"
    )
    now = "datetime('now','localtime')"
    con.execute(
        f"INSERT INTO competitor_products_store(competitor,product_name,price,product_url,"
        f"first_seen_at,updated_at,availability) VALUES "
        f"('س1','منتج جديد',100,'u1',{now},{now},1),"
        f"('س1','منتج نافذ',80,'u2',datetime('now','-9 days'),datetime('now','-9 days'),0),"
        f"('س1','منتج قديم',50,'u3',datetime('now','-20 days'),{now},1)"
    )
    con.execute(
        "CREATE TABLE scrape_runs(id INTEGER PRIMARY KEY, competitor TEXT, run_at TEXT, "
        "total_seen INTEGER, new_count INTEGER, oos_count INTEGER)"
    )
    con.execute(
        f"INSERT INTO scrape_runs(competitor,run_at,total_seen,new_count,oos_count) "
        f"VALUES ('س1',{now},3,1,1)"
    )
    con.commit(); con.close()

    svc = ScraperService(links_file=tmp_path / "c.json", competitor_db=db)
    # جديد ضمن آخر 7 أيام = المنتج الجديد فقط (النافذ availability=0 يُستبعد)
    new7 = svc.new_products(competitor="س1", since_days=7)
    assert [r["product_name"] for r in new7] == ["منتج جديد"]
    # نفذت = availability=0
    oos = svc.out_of_stock(competitor="س1")
    assert [r["product_name"] for r in oos] == ["منتج نافذ"]
    # آخر كشطة
    run = svc.last_scrape_run("س1")
    assert run["new_count"] == 1 and run["oos_count"] == 1 and run["total_seen"] == 3
    assert svc.last_scrape_run("غير-موجود") == {}


def test_new_products_since_last_run_join(tmp_path) -> None:
    """فرع «منذ آخر كشطة» (since_days=None): JOIN آخر جولة لكل منافس —
    بعد آخر كشطة يظهر، قبلها لا، ومنافس بلا سجل كشط يُستبعد (سلوك NULL القديم)."""
    import sqlite3
    db = tmp_path / "comp.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, competitor TEXT, product_name TEXT, price REAL, "
        "image_url TEXT, product_url TEXT, brand TEXT, "
        "first_seen_at TEXT, updated_at TEXT, availability INTEGER DEFAULT 1)"
    )
    con.execute(
        "INSERT INTO competitor_products_store(competitor,product_name,price,product_url,"
        "first_seen_at,availability) VALUES "
        "('س1','بعد الكشطة',100,'u1',datetime('now','localtime'),1),"
        "('س1','قبل الكشطة',90,'u2',datetime('now','localtime','-2 days'),1),"
        "('س2','منافس بلا كشطات',70,'u3',datetime('now','localtime'),1)"
    )
    con.execute(
        "CREATE TABLE scrape_runs(id INTEGER PRIMARY KEY, competitor TEXT, run_at TEXT, "
        "total_seen INTEGER, new_count INTEGER, oos_count INTEGER)"
    )
    con.execute(
        "INSERT INTO scrape_runs(competitor,run_at) VALUES "
        "('س1',datetime('now','localtime','-1 hours'))"
    )
    con.commit(); con.close()

    svc = ScraperService(links_file=tmp_path / "c.json", competitor_db=db)
    got = svc.new_products()  # منذ آخر كشطة
    assert [r["product_name"] for r in got] == ["بعد الكشطة"]


def test_new_products_cache_rebuild_and_read(tmp_path) -> None:
    """هـ1-تابع: rebuild_new_products_cache يخزّن نفس نتيجة new_products() الحيّة؛
    new_products_cached يقرأها بلا JOIN حيّ ويحترم فلتر المنافس."""
    import sqlite3
    db = tmp_path / "comp.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, competitor TEXT, product_name TEXT, price REAL, "
        "image_url TEXT, product_url TEXT, brand TEXT, "
        "first_seen_at TEXT, updated_at TEXT, availability INTEGER DEFAULT 1)"
    )
    con.execute(
        "INSERT INTO competitor_products_store(competitor,product_name,price,product_url,"
        "first_seen_at,availability) VALUES "
        "('س1','بعد الكشطة',100,'u1',datetime('now','localtime'),1),"
        "('س1','قبل الكشطة',90,'u2',datetime('now','localtime','-2 days'),1),"
        "('س2','منافس بلا كشطات',70,'u3',datetime('now','localtime'),1)"
    )
    con.execute(
        "CREATE TABLE scrape_runs(id INTEGER PRIMARY KEY, competitor TEXT, run_at TEXT, "
        "total_seen INTEGER, new_count INTEGER, oos_count INTEGER)"
    )
    con.execute(
        "INSERT INTO scrape_runs(competitor,run_at) VALUES "
        "('س1',datetime('now','localtime','-1 hours'))"
    )
    con.commit(); con.close()

    svc = ScraperService(links_file=tmp_path / "c.json", competitor_db=db)
    # قبل البناء: لا كاش ⇒ رجوع آمن للاستعلام الحيّ (نفس النتيجة، لا صفحة فارغة خطأً)
    assert [r["product_name"] for r in svc.new_products_cached()] == ["بعد الكشطة"]
    n = svc.rebuild_new_products_cache()
    assert n == 1
    got = svc.new_products_cached()
    assert [r["product_name"] for r in got] == ["بعد الكشطة"]
    assert svc.new_products_cached(competitor="س2") == []  # فلتر المنافس يعمل على الكاش


def test_list_unlinked(tmp_path) -> None:
    """list_unlinked يعيد المنافسين بلا mahally_store_id فقط."""
    import json
    f = tmp_path / "comps.json"
    f.write_text(json.dumps([
        {"name": "مرتبط", "store_url": "https://mahally.com/stores/7/", "mahally_store_id": 7},
        {"name": "بلا معرّف", "store_url": "https://goldenscent.com/"},
        {"name": "بلا معرّف 2", "store_url": "https://niche.sa/", "mahally_store_id": 0},
    ], ensure_ascii=False), encoding="utf-8")
    svc = ScraperService(links_file=f)
    unlinked = svc.list_unlinked()
    names = {u["name"] for u in unlinked}
    assert names == {"بلا معرّف", "بلا معرّف 2"}      # المرتبط مُستبعد
    assert len(svc.list_competitors()) == 1            # المرتبط فقط في القائمة الأساسية


def test_legacy_database_source_with_mahally_url_is_recovered_in_memory(tmp_path) -> None:
    """سجل قديم يستعيد Mahally من الرابط فقط ولا يعيد كتابة JSON."""
    import json

    path = tmp_path / "competitors.json"
    path.write_text(json.dumps([{
        "name": "متجر Mahally قديم",
        "store_url": "https://mahally.com/stores/123456/",
        "mahally_store_id": None,
        "source": "pricing_v18.db",
    }], ensure_ascii=False), encoding="utf-8")

    listed = ScraperService(links_file=path).list_competitors()

    assert [(c.source, c.mahally_store_id) for c in listed] == [("mahally", 123456)]
    assert json.loads(path.read_text(encoding="utf-8"))[0]["source"] == "pricing_v18.db"


def test_legacy_database_source_without_mahally_identifier_is_skipped(tmp_path, caplog) -> None:
    """السجل الغامض لا يمرر إلى router ولا يُحوّل إلى Mahally بالتخمين."""
    import json

    path = tmp_path / "competitors.json"
    path.write_text(json.dumps([{
        "name": "متجر غامض",
        "store_url": "https://example.com/shop",
        "mahally_store_id": None,
        "source": "/data/pricing_v18.db",
    }], ensure_ascii=False), encoding="utf-8")

    assert ScraperService(links_file=path).list_competitors() == []
    assert "legacy_source_ambiguous" in caplog.text


def test_supported_source_is_not_changed_by_legacy_recovery(tmp_path) -> None:
    """المصدر المدعوم يحتفظ بقيمته الحالية حتى إذا لم يحمل معرّف Mahally."""
    import json

    path = tmp_path / "competitors.json"
    path.write_text(json.dumps([{
        "name": "متجر Noon",
        "store_url": "https://www.noon.com/saudi-en/",
        "mahally_store_id": None,
        "source": "noon",
    }], ensure_ascii=False), encoding="utf-8")

    listed = ScraperService(links_file=path).list_competitors()
    assert [(c.source, c.mahally_store_id) for c in listed] == [("noon", 0)]


def test_unknown_source_fails_safely_without_normalization(tmp_path, caplog) -> None:
    """مصدر مجهول يبقى مجهولاً ثم يعيد router صفراً بلا شبكة أو تخمين."""
    import json

    path = tmp_path / "competitors.json"
    path.write_text(json.dumps([{
        "name": "متجر مجهول",
        "store_url": "https://example.com/unknown",
        "mahally_store_id": None,
        "source": "unknown_marketplace",
    }], ensure_ascii=False), encoding="utf-8")

    service = ScraperService(links_file=path)
    listed = service.list_competitors()
    assert len(listed) == 1 and listed[0].source == "unknown_marketplace"
    assert service.scrape_and_save(listed[0]) == 0
    assert "Unsupported source" in caplog.text


def test_legacy_database_source_rejects_non_mahally_host(tmp_path, caplog) -> None:
    """مسار /stores/ في نطاق غير Mahally لا يكفي للاسترداد."""
    import json

    path = tmp_path / "competitors.json"
    path.write_text(json.dumps([{
        "name": "نطاق غير موثوق",
        "store_url": "https://example.com/stores/123456",
        # حتى المعرّف القديم لا يعتمد عندما لا يثبت نطاق الرابط Mahally.
        "mahally_store_id": 777,
        "source": "pricing_v18.db",
    }], ensure_ascii=False), encoding="utf-8")

    assert ScraperService(links_file=path).list_competitors() == []
    assert "legacy_source_ambiguous" in caplog.text


@pytest.mark.parametrize("source_value", [None, ""])
def test_explicit_empty_source_is_not_inferred_as_mahally(tmp_path, source_value) -> None:
    """وجود source بقيمة None أو فارغة لا يرث الافتراضي ولا يطبع المصدر."""
    import json

    path = tmp_path / "competitors.json"
    path.write_text(json.dumps([{
        "name": "مصدر فارغ",
        "store_url": "https://mahally.com/stores/123456/",
        "mahally_store_id": None,
        "source": source_value,
    }], ensure_ascii=False), encoding="utf-8")

    listed = ScraperService(links_file=path).list_competitors()
    assert len(listed) == 1
    assert listed[0].source == ""
    assert listed[0].mahally_store_id == 0
