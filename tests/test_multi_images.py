# -*- coding: utf-8 -*-
"""الصور المتعددة + rating_avg في الكاشط (خطوة multi-images).

image_urls يُخزَّن نصّ JSON (يفكّه _norm_image_urls/make_helper وقارئ
competitor_intelligence يحمله كما هو). rating_avg=None حين لا تقييم.
"""
import json
import sqlite3

from engines.mahally_scraper import MahallyScraper
from engines.competitor_intelligence import CompetitorIntelligence

_S = MahallyScraper(db_path=None)
_N = "عطر اختبار"


def test_parse_hit_image_urls_main_first_dedup_cap():
    """all_images متعددة مع تكرار ⇒ الرئيسية أولاً، بلا تكرار، ضمن سقف 10."""
    hit = {
        "name": _N, "price": 100, "image": "https://c/main.jpg",
        "all_images": ["https://c/a.jpg", "https://c/main.jpg",
                       "https://c/b.jpg", "https://c/a.jpg"],
        "all_rating": {"count": 5, "average": 4.3},
    }
    urls = json.loads(_S._parse_hit(hit, "T")["image_urls"])
    assert urls == ["https://c/main.jpg", "https://c/a.jpg", "https://c/b.jpg"]
    # السقف
    many = {"name": _N, "price": 100, "image": "https://c/m.jpg",
            "all_images": [f"https://c/{i}.jpg" for i in range(20)]}
    assert len(json.loads(_S._parse_hit(many, "T")["image_urls"])) == 10


def test_parse_hit_no_all_images_and_rating_none():
    """بلا all_images ⇒ الرئيسية فقط (JSON صالح)؛ بلا صور ⇒ ''؛
    عدّ التقييم صفر ⇒ rating_avg = None (لا يختلط «بلا تقييم» بـ«صفر»)."""
    r = _S._parse_hit({"name": _N, "price": 100, "image": "https://c/m.jpg",
                       "all_rating": {"count": 0, "average": 0}}, "T")
    assert json.loads(r["image_urls"]) == ["https://c/m.jpg"]
    assert r["rating_avg"] is None
    r2 = _S._parse_hit({"name": _N, "price": 100, "image": ""}, "T")
    assert r2["image_urls"] == ""


def test_save_and_read_end_to_end(tmp_path):
    """حفظ في قاعدة مؤقتة ⇒ العمودان مكتوبان، وقارئ competitor_intelligence
    الفعلي (get_best_sellers) يفكّ image_urls بنجاح."""
    db = str(tmp_path / "e2e.db")
    scraper = MahallyScraper(db_path=db)
    urls = json.dumps(["https://c/main.jpg", "https://c/a.jpg"])
    prod = {
        "name": _N, "price": 100.0, "availability": True, "image": "https://c/main.jpg",
        "url": "", "brand": "", "rating_count": 5, "rating_avg": 4.3,
        "discount_pct": 0, "original_price": 0, "category": "", "sku": "",
        "image_urls": urls,
    }
    scraper.save_to_db([prod], "متجر اختبار")
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT image_urls, rating_avg FROM competitor_products_store"
    ).fetchone()
    con.close()
    assert row[0] == urls and row[1] == 4.3           # العمودان مكتوبان
    res, total = CompetitorIntelligence(db_path=db).get_best_sellers()
    assert total == 1
    assert json.loads(res[0]["image_urls"]) == ["https://c/main.jpg", "https://c/a.jpg"]
    assert res[0]["rating_avg"] == 4.3
