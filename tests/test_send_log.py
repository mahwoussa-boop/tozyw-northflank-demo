"""tests/test_send_log.py — سجل الإرسال (send_log) + حارس تكرار الـSKU.

يثبّت:
- تدوين نجاح/فشل الإرسال بالحقول الصحيحة.
- قصّ المقتطف إلى ≤200 حرف وإزالة رابط الويبهوك منه (أمان).
- كشف التكرار عبر السجل (success=1,new) وعبر our_catalog.
- تكامل make_helper: تدوين كل منتج جديد + تخطّي المكرر (بلا إرسال) +
  عدم كسر الإرسال عند فشل التدوين.

كل «إرسال» هنا بويبهوك مزيّف (monkeypatch لـ _post_to_webhook) — لا POST حقيقي.
"""
import sqlite3

import utils.make_helper as mh
import utils.product_gate as pg
import utils.send_log as sl


# ── أدوات مساعدة ───────────────────────────────────────────────────────────
def _rows(db, cols="webhook_type,sku,success,http_status"):
    return sqlite3.connect(db).execute(f"SELECT {cols} FROM send_log ORDER BY id").fetchall()


def _use_tmp_db(monkeypatch, tmp_path):
    """يوجّه send_log إلى قاعدة مؤقتة (لا تلوّث pricing_v18.db الحقيقية)."""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(sl, "get_data_db_path", lambda *_: db)
    sl.init_send_log(db)
    return db


def _pass_gate(monkeypatch):
    """يجعل بوابة الجودة تمرّر المنتجات كما هي (بلا صورة/AI)."""
    monkeypatch.setattr(pg, "validate_and_enrich", lambda products, **kw: (list(products), []))
    monkeypatch.setattr(pg, "is_mahwous_description", lambda *_a, **_k: True)


# ── وحدة: log_send ─────────────────────────────────────────────────────────
def test_log_send_records_success(tmp_path):
    db = str(tmp_path / "t.db")
    sl.init_send_log(db)
    sl.log_send("new", sku="SK1", product_name="عطر", category="عطور",
                brand="شانيل", http_status=200, success=True,
                response_excerpt="ok", batch_id="B1", db_path=db)
    rows = sqlite3.connect(db).execute(
        "SELECT webhook_type,sku,product_name,category,brand,http_status,success FROM send_log"
    ).fetchall()
    assert rows == [("new", "SK1", "عطر", "عطور", "شانيل", 200, 1)]


def test_log_send_records_failure(tmp_path):
    db = str(tmp_path / "t.db")
    sl.init_send_log(db)
    sl.log_send("update", sku="SK2", http_status=422, success=False,
                response_excerpt="HTTP 422 bad", db_path=db)
    assert _rows(db) == [("update", "SK2", 0, 422)]


def test_log_send_strips_webhook_url_and_truncates(tmp_path):
    db = str(tmp_path / "t.db")
    sl.init_send_log(db)
    long_tail = "x" * 400
    sl.log_send("new", sku="SK3", success=True,
                response_excerpt=f"ok https://hook.eu2.make.com/SECRET {long_tail}",
                db_path=db)
    (excerpt,) = sqlite3.connect(db).execute(
        "SELECT response_excerpt FROM send_log WHERE sku='SK3'").fetchone()
    assert "hook" not in excerpt and "SECRET" not in excerpt
    assert len(excerpt) <= 200


# ── وحدة: is_duplicate_new_sku ─────────────────────────────────────────────
def test_is_duplicate_new_sku_true_after_success(tmp_path):
    db = str(tmp_path / "t.db")
    sl.init_send_log(db)
    sl.log_send("new", sku="DUP", success=True, http_status=200, db_path=db)
    assert sl.is_duplicate_new_sku("DUP", db_path=db) is True
    assert sl.is_duplicate_new_sku("OTHER", db_path=db) is False
    assert sl.is_duplicate_new_sku("", db_path=db) is False


def test_failed_new_send_is_not_duplicate(tmp_path):
    db = str(tmp_path / "t.db")
    sl.init_send_log(db)
    sl.log_send("new", sku="FAIL", success=False, http_status=500, db_path=db)
    # فشل الإرسال لا يُعدّ «مُرسَلاً» ⇒ يُعاد إرساله (ليس مكرراً)
    assert sl.is_duplicate_new_sku("FAIL", db_path=db) is False


def test_is_duplicate_via_our_catalog(tmp_path):
    db = str(tmp_path / "t.db")
    sl.init_send_log(db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE our_catalog (product_id TEXT UNIQUE, product_name TEXT)")
    conn.execute("INSERT INTO our_catalog(product_id,product_name) VALUES('CAT9','منتج')")
    conn.commit(); conn.close()
    assert sl.is_duplicate_new_sku("CAT9", db_path=db) is True
    assert sl.is_duplicate_new_sku("NOPE", db_path=db) is False


# ── تكامل make_helper: تدوين المنتجات الجديدة ─────────────────────────────
_NEW = {"name": "عطر أ", "sku": "SKU-A", "price": 100, "الوصف": "<p>وصف مهووس</p>"}


def test_send_new_products_writes_success_row(monkeypatch, tmp_path):
    db = _use_tmp_db(monkeypatch, tmp_path)
    _pass_gate(monkeypatch)
    monkeypatch.setattr(mh, "_post_to_webhook",
                        lambda url, payload: {"success": True, "status_code": 200, "message": "✅ ok"})
    out = mh.send_new_products([dict(_NEW)])
    assert out["sent"] == 1
    assert _rows(db) == [("new", "SKU-A", 1, 200)]


def test_send_new_products_writes_failure_row(monkeypatch, tmp_path):
    db = _use_tmp_db(monkeypatch, tmp_path)
    _pass_gate(monkeypatch)
    monkeypatch.setattr(mh, "_post_to_webhook",
                        lambda url, payload: {"success": False, "status_code": 422, "message": "❌ HTTP 422"})
    out = mh.send_new_products([dict(_NEW)])
    assert out["sent"] == 0
    assert _rows(db) == [("new", "SKU-A", 0, 422)]


def test_send_new_products_skips_duplicate_no_post(monkeypatch, tmp_path):
    db = _use_tmp_db(monkeypatch, tmp_path)
    _pass_gate(monkeypatch)
    sl.log_send("new", sku="SKU-A", success=True, http_status=200, db_path=db)  # أُرسل سابقاً
    posted = []
    monkeypatch.setattr(mh, "_post_to_webhook",
                        lambda url, payload: posted.append(payload) or {"success": True, "status_code": 200})
    out = mh.send_new_products([dict(_NEW)])
    assert posted == []          # لم يُرسَل المكرر
    assert out["sent"] == 0
    excerpts = [r[0] for r in sqlite3.connect(db).execute(
        "SELECT response_excerpt FROM send_log WHERE sku='SKU-A'").fetchall()]
    assert "skipped_duplicate" in excerpts


def test_logging_failure_does_not_break_send(monkeypatch, tmp_path):
    _pass_gate(monkeypatch)
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sl, "log_send", _boom)
    monkeypatch.setattr(sl, "is_duplicate_new_sku", lambda *a, **k: False)
    monkeypatch.setattr(mh, "_post_to_webhook",
                        lambda url, payload: {"success": True, "status_code": 200, "message": "ok"})
    out = mh.send_new_products([dict(_NEW)])
    assert out["success"] is True and out["sent"] == 1   # التدوين الفاشل لم يمنع الإرسال


# ── تكامل make_helper: تدوين تحديث الأسعار (صف لكل منتج + السعر المُرسَل) ───
def test_send_price_updates_writes_row_per_product(monkeypatch, tmp_path):
    db = _use_tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(mh, "_post_to_webhook",
                        lambda url, payload: {"success": True, "status_code": 200, "message": "✅ ok"})
    out = mh.send_price_updates([
        {"name": "عطر", "product_id": "5", "NO": "5", "price": 50},
        {"name": "عود", "product_id": "6", "NO": "6", "price": 70},
    ])
    assert out["success"] is True
    assert _rows(db, "webhook_type,sku,price,success,http_status") == [
        ("update", "5", 50.0, 1, 200), ("update", "6", 70.0, 1, 200)]


# ── الهجرة: قاعدة قديمة بلا عمودَي السعر تُرقَّى، وغياب السعر ⇒ NULL لا 0 ──
def test_migration_adds_price_columns_and_null_default(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE send_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, batch_id TEXT, webhook_type TEXT,
        sku TEXT, product_name TEXT, category TEXT, brand TEXT,
        http_status INTEGER, success INTEGER DEFAULT 0, response_excerpt TEXT)""")
    conn.commit(); conn.close()
    sl.init_send_log(db)   # الهجرة تضيف price/comp_price للقاعدة القديمة
    sl.log_send("update", sku="M1", price=50, comp_price=51, success=True, db_path=db)
    sl.log_send("update", sku="M2", success=True, db_path=db)   # بلا سعر ⇒ NULL
    rows = sqlite3.connect(db).execute(
        "SELECT sku,price,comp_price FROM send_log ORDER BY id").fetchall()
    assert rows == [("M1", 50.0, 51.0), ("M2", None, None)]
