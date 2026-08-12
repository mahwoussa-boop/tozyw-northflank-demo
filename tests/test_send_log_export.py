"""tests/test_send_log_export.py — تدوين وحراسة مسار ExportService.post_to_make.

يثبّت أن مسار أزرار الواجهة (المتجاوز لـmake_helper) صار يُدوَّن في send_log:
- تدوين المنتجات الجديدة (data) صفاً لكل منتج بمفاتيح سلة العربية.
- تدوين التحديث (products) صفاً لكل منتج مع السعر المُرسَل (العمود الفقري للتغذية الراجعة).
- حارس تكرار الـSKU على حمولات data فقط (إسقاط + skipped_duplicate بلا POST).
- صمود الإرسال عند فشل التدوين + تدوين فشل الاتصال.

الإرسال بـposter محقون (لا شبكة) — لا POST حقيقي إطلاقاً.
"""
import sqlite3

import utils.send_log as sl
from services.export_service import ExportService


class _Resp:
    def __init__(self, code, text="ok"):
        self.status_code = code
        self.text = text


def _tmp_db(monkeypatch, tmp_path):
    """يوجّه send_log إلى قاعدة مؤقتة (لا تلوّث pricing_v18.db)."""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(sl, "get_data_db_path", lambda *_: db)
    sl.init_send_log(db)
    return db


def _svc(posts):
    return ExportService(
        poster=lambda url, json, headers, timeout: posts.append(json) or _Resp(
            200, '{"ok": true, "product_id": 123}'))


def _rows(db, cols="webhook_type,sku,success,http_status"):
    return sqlite3.connect(db).execute(f"SELECT {cols} FROM send_log ORDER BY id").fetchall()


_NEW = {"أسم المنتج": "عطر ج", "رمز المنتج sku": "EX-1", "اسم التصنيف": "عطور", "الماركة": "ديور"}


def test_post_to_make_logs_new(monkeypatch, tmp_path):
    db = _tmp_db(monkeypatch, tmp_path)
    posts = []
    _svc(posts).post_to_make("http://hook", [dict(_NEW)], envelope="data")
    rows = sqlite3.connect(db).execute(
        "SELECT webhook_type,sku,product_name,category,brand,success FROM send_log").fetchall()
    assert rows == [("new", "EX-1", "عطر ج", "عطور", "ديور", 1)]
    assert len(posts) == 1


def test_post_to_make_logs_update_row_per_product(monkeypatch, tmp_path):
    db = _tmp_db(monkeypatch, tmp_path)
    posts = []
    _svc(posts).post_to_make(
        "http://hook",
        [{"name": "A", "product_id": "5", "price": 50.0},
         {"name": "B", "product_id": "6", "price": 60.0}],
        envelope="products")
    assert _rows(db, "webhook_type,sku,price,success,http_status") == [
        ("update", "5", 50.0, 1, 200), ("update", "6", 60.0, 1, 200)]
    assert len(posts) == 1  # POST واحد للدفعة — والتدوين صف لكل منتج (العمود الفقري)


def test_post_to_make_skips_duplicate_no_post(monkeypatch, tmp_path):
    db = _tmp_db(monkeypatch, tmp_path)
    sl.log_send("new", sku="EX-1", success=True, http_status=200, db_path=db)  # مُرسَل سابقاً
    posts = []
    res = _svc(posts).post_to_make("http://hook", [dict(_NEW)], envelope="data")
    assert posts == []                          # لم يُرسَل المكرر
    assert res.get("skipped_duplicate") is True
    excerpts = [r[0] for r in sqlite3.connect(db).execute(
        "SELECT response_excerpt FROM send_log WHERE sku='EX-1'").fetchall()]
    assert "skipped_duplicate" in excerpts


def test_post_to_make_logging_failure_does_not_break_send(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sl, "log_send", _boom)
    monkeypatch.setattr(sl, "is_duplicate_new_sku", lambda *a, **k: False)
    posts = []
    res = _svc(posts).post_to_make("http://hook", [dict(_NEW)], envelope="data")
    assert res["success"] is True and len(posts) == 1   # التدوين الفاشل لم يمنع الإرسال


def test_post_to_make_logs_connection_failure(monkeypatch, tmp_path):
    db = _tmp_db(monkeypatch, tmp_path)
    def _raise(url, json, headers, timeout):
        raise ConnectionError("no net")
    res = ExportService(poster=_raise).post_to_make(
        "http://hook", [{"name": "A", "product_id": "5"}], envelope="products")
    assert res["success"] is False
    assert _rows(db, "webhook_type,success") == [("update", 0)]
