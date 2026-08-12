"""اختبار عدّاد «نفذت» الصادق (COUNT مباشر) — تخفيف الأداء (الخطوة ٣).

يتحقّق أن ``out_of_stock_count`` يطابق العدد الحقيقي على عيّنة معروفة (بلا سقف)،
ويساوي طول ``out_of_stock`` على عيّنة صغيرة — بنفس شرط WHERE.
"""
import sqlite3

from services.scraper_service import ScraperService


def _make_db(tmp_path, spec):
    """يبني قاعدة منافسين مؤقتة: spec = {competitor: (عدد_نافذ, عدد_متوفر)}."""
    db = tmp_path / "comp.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, competitor TEXT, product_name TEXT, price REAL, "
        "image_url TEXT, product_url TEXT, brand TEXT, "
        "first_seen_at TEXT, updated_at TEXT, availability INTEGER DEFAULT 1)"
    )
    now = "datetime('now','localtime')"
    rid = 0
    for comp, (n_oos, n_avail) in spec.items():
        for avail, count in ((0, n_oos), (1, n_avail)):
            for _ in range(count):
                rid += 1
                con.execute(
                    "INSERT INTO competitor_products_store("
                    "competitor,product_name,price,product_url,"
                    f"first_seen_at,updated_at,availability) VALUES (?,?,?,?,{now},{now},?)",
                    (comp, f"p{rid}", 10, f"u{rid}", avail),
                )
    con.commit()
    con.close()
    return db


def test_out_of_stock_count_matches_real(tmp_path):
    db = _make_db(tmp_path, {"س1": (5, 3), "س2": (7, 2)})
    svc = ScraperService(links_file=tmp_path / "c.json", competitor_db=db)
    # الإجمالي الحقيقي = 5 + 7 نافذ (المتوفر availability=1 مُستبعَد)
    assert svc.out_of_stock_count() == 12
    # مفلتر بمنافس — يطابق العدّ المعروف
    assert svc.out_of_stock_count(competitor="س1") == 5
    assert svc.out_of_stock_count(competitor="س2") == 7
    # يساوي طول out_of_stock (نفس WHERE) على عيّنة أصغر من السقف
    assert svc.out_of_stock_count(competitor="س1") == len(
        svc.out_of_stock(competitor="س1", limit=10000)
    )
    # منافس غير موجود = 0 (آمن)
    assert svc.out_of_stock_count(competitor="لا-أحد") == 0
