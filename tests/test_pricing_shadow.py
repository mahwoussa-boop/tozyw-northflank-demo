"""tests/test_pricing_shadow.py — السياج الظِّلّي (م2): تدوين فقط، لا تغيير سعر.

يثبّت:
- ``verdict_for`` الخالصة: ok / below_band / above_band / stale_market / no_market_row.
- ``record_shadow``: صف لكل عنصر بالحكم الصحيح من ``opportunity_scores`` مصغّر.
- غياب صف السوق ⇒ no_market_row؛ العناصر الفاسدة/section=test تُتجاوز؛ لا استثناء أبداً.
- العقفان يستدعيان الظلّ: ``export_service.post_to_make`` و``make_helper._post_to_webhook``
  — والحمولة المُرسَلة **غير ملموسة** (لا تغيير سعر).
"""
import sqlite3

import services.pricing_shadow as ps


def _mk_db(tmp_path, rows):
    """قاعدة مصغّرة بجدول opportunity_scores بالأعمدة التي يقرأها الظلّ فقط."""
    db = str(tmp_path / "o.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE opportunity_scores (
        norm_name TEXT, suggested_price REAL, price_median REAL,
        instock_price_min REAL, data_age_days REAL, stale_reason TEXT)""")
    conn.executemany("INSERT INTO opportunity_scores VALUES (?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()
    return db


def _shadow_rows(db, cols="path,sku,name,sent_price,verdict"):
    return sqlite3.connect(db).execute(
        f"SELECT {cols} FROM pricing_guard_shadow ORDER BY id").fetchall()


# ── الحكم الخالص ────────────────────────────────────────────────────────────
def test_verdict_pure():
    assert ps.verdict_for(100, 100) == "ok"
    assert ps.verdict_for(79.9, 100) == "below_band"
    assert ps.verdict_for(120.1, 100) == "above_band"
    assert ps.verdict_for(100, 100, stale_reason="stale") == "stale_market"
    assert ps.verdict_for(100, None) == "no_market_row"
    assert ps.verdict_for(100, 0) == "no_market_row"


# ── التدوين من قاعدة مصغّرة ─────────────────────────────────────────────────
def test_record_shadow_writes_verdicts(tmp_path):
    from utils.db_manager import _normalize_for_store
    name = "عطر ديور سوفاج 100 مل"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 99.0, 100.0, 100.0, 1.0, None)])
    n = ps.record_shadow(
        [{"name": name, "NO": "7", "price": 70.0},    # 70 < 80 ⇒ below_band
         {"name": name, "NO": "8", "price": 95.0}],   # داخل النطاق ⇒ ok
        path="helper", db_path=db)
    assert n == 2
    assert _shadow_rows(db) == [
        ("helper", "7", name, 70.0, "below_band"),
        ("helper", "8", name, 95.0, "ok")]


def test_record_shadow_stale_market(tmp_path):
    from utils.db_manager import _normalize_for_store
    name = "عطر قديم البيانات"
    db = _mk_db(tmp_path, [(_normalize_for_store(name), None, 100.0, 100.0, 9.0, "stale")])
    ps.record_shadow([{"name": name, "price": 95.0}], path="helper", db_path=db)
    assert _shadow_rows(db, "verdict") == [("stale_market",)]


def test_record_shadow_no_market_row_and_skips_garbage(tmp_path):
    db = _mk_db(tmp_path, [])
    n = ps.record_shadow(
        [{"name": "منتج مجهول تماماً", "price": 50.0},
         {"name": "", "price": 10},                              # بلا اسم ⇒ يُتجاوز
         {"name": "بلا سعر"},                                     # بلا سعر ⇒ يُتجاوز
         {"name": "اختبار الاتصال", "price": 1.0, "section": "test"},  # test ⇒ يُتجاوز
         "ليس قاموساً"],                                          # فاسد ⇒ يُتجاوز
        path="export.update", db_path=db)
    assert n == 1
    assert _shadow_rows(db, "verdict") == [("no_market_row",)]


def test_record_shadow_never_raises_on_missing_db(tmp_path):
    # قاعدة غير موجودة: القراءة ro تفشل ⇒ يُبتلع ويعيد 0 — لا استثناء أبداً.
    n = ps.record_shadow([{"name": "س", "price": 5.0}], path="x",
                         db_path=str(tmp_path / "missing.db"))
    assert n == 0


# ── العقفان يستدعيان الظلّ ولا يلمسان الحمولة ───────────────────────────────
class _Resp:
    status_code = 200
    text = "ok"


def test_export_post_to_make_calls_shadow(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ps, "record_shadow",
                        lambda items, path, **k: calls.append((list(items), path)) or 0)
    import utils.send_log as sl
    monkeypatch.setattr(sl, "get_data_db_path", lambda *_: str(tmp_path / "t.db"))
    sl.init_send_log(str(tmp_path / "t.db"))
    from services.export_service import ExportService

    posts = []
    svc = ExportService(
        poster=lambda url, json, headers, timeout: posts.append(json) or _Resp())
    items = [{"name": "A", "product_id": "5", "price": 50.0}]
    svc.post_to_make("http://hook", items, envelope="products")
    assert calls and calls[0][1] == "export.update"
    assert posts == [{"products": items}]   # الحمولة غير ملموسة بايتاً


def test_helper_post_to_webhook_calls_shadow(monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "record_shadow",
                        lambda items, path, **k: calls.append(path) or 0)
    import utils.make_helper as mh
    monkeypatch.setattr(mh.requests, "post", lambda *a, **k: _Resp())
    res = mh._post_to_webhook("http://hook", {"products": [{"name": "x", "price": 9.0}]})
    assert res["success"] is True
    assert calls == ["helper"]
