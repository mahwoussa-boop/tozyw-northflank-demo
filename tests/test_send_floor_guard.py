"""tests/test_send_floor_guard.py — أرضية «خطأ الكشط» على مسار الإرسال (وحدة أ2).

يثبّت:
- ``split_below_floor`` الخالصة: يحجز سعراً < 50% من وسيط السوق، يمرّر الباقي،
  لا يحجب بلا صفّ سوق (no_market_row)، fail-open عند خطأ قاعدة.
- ``FLOOR_RATIO=0`` يعطّل الحجب فوراً (مسار التراجع الموثَّق في الخطة).
- العقفان يطبّقان الحجب فعلياً: ``export_service.post_to_make`` و
  ``make_helper._post_to_webhook`` — لا POST لعنصر محجوز، والحمولة المتبقية سليمة.
- ``send_price_updates``: عنصر محجوز **لا يدخل send_log بسعره المحجوز** أبداً
  (المقياس الرقمي للوحدة يعتمد على نظافة send_log — راجع docs/خطة-التطوير-الشاملة.md §أ2).
"""
import sqlite3

import pytest

import services.send_floor_guard as sfg
import utils.make_helper as mh
import utils.send_log as _sl
from services.export_service import ExportService
from services.send_floor_guard import split_below_floor
from utils.db_manager import _normalize_for_store


@pytest.fixture(autouse=True)
def _isolate_send_log(monkeypatch, tmp_path):
    """يعزل ``send_log`` إلى قاعدة مؤقتة لكل اختبار في هذا الملف.

    اختبار ``send_price_updates`` يتحقّق من نظافة ``send_log`` — وكان يفحصها في
    ``pricing_v18.db`` الحيّ ويكتب فيها صفّاً وهمياً («عطر شانيل»/190) مع **كل
    تشغيل للبوّابة**، أي مع كل commit عبر خطّاف pre-commit. قِيس حيّاً 2026-07-26:
    1,657 ⇒ 1,658 من تشغيل هذا الملف وحده.
    نفس عزل ``tests/test_export_service.py:_isolate_send_log``."""
    db = str(tmp_path / "sendlog.db")
    monkeypatch.setattr(_sl, "get_data_db_path", lambda *_a, **_k: db)
    _sl.init_send_log(db)


def _mk_db(tmp_path, rows, filename="market.db"):
    """قاعدة مصغّرة بجدول opportunity_scores (عمود price_median فقط — كل ما يقرأه الحارس)."""
    db = str(tmp_path / filename)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE opportunity_scores (norm_name TEXT, price_median REAL)")
    conn.executemany("INSERT INTO opportunity_scores VALUES (?,?)", rows)
    conn.commit()
    conn.close()
    return db


# ── الدالة الخالصة ───────────────────────────────────────────────────────────
def test_split_below_floor_blocks_below_50pct(tmp_path):
    name = "عطر ديور سوفاج 100 مل"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 100.0)])
    allowed, blocked = split_below_floor(
        [{"name": name, "NO": "1", "price": 40.0},   # 40 < 50 ⇒ محجوز
         {"name": name, "NO": "2", "price": 60.0}],  # 60 ≥ 50 ⇒ يمرّ (فوق النطاق أيضاً يمرّ)
        db_path=db,
    )
    assert [a["NO"] for a in allowed] == ["2"]
    assert len(blocked) == 1 and blocked[0]["NO"] == "1"
    assert "blocked_reason" in blocked[0] and "50" in blocked[0]["blocked_reason"]


def test_split_below_floor_exact_boundary_passes(tmp_path):
    # الحد بالضبط (=50%) ليس «أقل من» ⇒ يمرّ (مقارنة صارمة <، لا ≤).
    name = "منتج الحد"
    db = _mk_db(tmp_path, [(_normalize_for_store(name), 100.0)])
    allowed, blocked = split_below_floor(
        [{"name": name, "price": 50.0}], db_path=db)
    assert len(allowed) == 1 and blocked == []


def test_split_below_floor_no_market_row_never_blocks(tmp_path):
    db = _mk_db(tmp_path, [])  # لا صفوف سوق إطلاقاً
    allowed, blocked = split_below_floor(
        [{"name": "منتج مجهول تماماً", "price": 1.0}], db_path=db)
    assert len(allowed) == 1 and blocked == []


def test_split_below_floor_skips_garbage_items(tmp_path):
    # عناصر فاسدة (بلا اسم/سعر أو غير dict أصلاً) تمرّ كما هي بلا حجب — لا يقين
    # سعري بلا اسم/سعر صالح، وغير-dict تتجاوز فحص النوع فتُعامَل كمسموحة دائماً.
    db = _mk_db(tmp_path, [])
    allowed, blocked = split_below_floor(
        [{"name": "", "price": 10}, {"name": "بلا سعر"}, "ليس قاموساً", None],
        db_path=db,
    )
    assert len(allowed) == 4 and blocked == []


def test_split_below_floor_fail_open_on_missing_db(tmp_path):
    allowed, blocked = split_below_floor(
        [{"name": "س", "price": 1.0}], db_path=str(tmp_path / "missing.db"))
    assert len(allowed) == 1 and blocked == []


def test_split_below_floor_ratio_zero_disables_guard(tmp_path, monkeypatch):
    name = "عطر أ"
    db = _mk_db(tmp_path, [(_normalize_for_store(name), 100.0)])
    monkeypatch.setattr(sfg, "FLOOR_RATIO", 0.0)
    allowed, blocked = split_below_floor(
        [{"name": name, "price": 1.0}], db_path=db)  # سعر كارثي لكن العتبة معطّلة
    assert len(allowed) == 1 and blocked == []


# ── عقف export_service.post_to_make ─────────────────────────────────────────
def test_post_to_make_blocks_low_price_item(tmp_path, monkeypatch):
    """يعتمد على ``_shadow_db_isolated`` (autouse) الذي يوجّه ``_default_db``
    لنفس ``tmp_path`` — إنشاء الملف هنا يجعل الحارس يقرأ سوقاً حقيقياً."""
    name = "عطر شانيل"
    db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE opportunity_scores (norm_name TEXT, price_median REAL)")
    conn.execute("INSERT INTO opportunity_scores VALUES (?,?)", (_normalize_for_store(name), 200.0))
    conn.commit(); conn.close()

    captured = {}

    def poster(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        class _Resp:
            status_code = 200
        return _Resp()

    svc = ExportService(poster=poster)
    result = svc.post_to_make("http://hook", [
        {"NO": "1", "name": name, "price": 50.0},    # 50 < 100 (50%×200) ⇒ محجوز
        {"NO": "2", "name": name, "price": 190.0},   # يمرّ
    ])
    assert result["success"] is True
    assert captured["payload"] == {"products": [{"NO": "2", "name": name, "price": 190.0}]}
    assert len(result["blocked_low_price"]) == 1
    assert result["blocked_low_price"][0]["NO"] == "1"


def test_post_to_make_all_blocked_skips_post_entirely(tmp_path):
    name = "عطر الكل محجوز"
    db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE opportunity_scores (norm_name TEXT, price_median REAL)")
    conn.execute("INSERT INTO opportunity_scores VALUES (?,?)", (_normalize_for_store(name), 300.0))
    conn.commit(); conn.close()

    posted = []

    def poster(url, **kw):
        posted.append(url)
        class _Resp:
            status_code = 200
        return _Resp()

    svc = ExportService(poster=poster)
    result = svc.post_to_make("http://hook", [{"NO": "1", "name": name, "price": 10.0}])
    assert posted == []  # صفر POST — كل العناصر محجوزة
    assert result["success"] is True
    assert len(result["blocked_low_price"]) == 1


# ── عقف make_helper._post_to_webhook ────────────────────────────────────────
def test_post_to_webhook_filters_payload_before_post(tmp_path, monkeypatch):
    name = "عود كمبودي"
    db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE opportunity_scores (norm_name TEXT, price_median REAL)")
    conn.execute("INSERT INTO opportunity_scores VALUES (?,?)", (_normalize_for_store(name), 400.0))
    conn.commit(); conn.close()

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(mh.requests, "post", fake_post)
    payload = {"products": [
        {"NO": "9", "name": name, "price": 50.0},    # 50 < 200 (50%×400) ⇒ محجوز
        {"NO": "10", "name": name, "price": 380.0},  # يمرّ
    ]}
    result = mh._post_to_webhook("http://hook", payload)
    assert result["success"] is True
    assert captured["json"]["products"] == [{"NO": "10", "name": name, "price": 380.0}]
    assert len(result["blocked_low_price"]) == 1 and result["blocked_low_price"][0]["NO"] == "9"


# ── send_price_updates: send_log يبقى نظيفاً من الأسعار المحجوزة ────────────
def test_send_price_updates_never_logs_blocked_price(tmp_path, monkeypatch):
    """الاختبار الحرج للمقياس الرقمي: صفر صفّ send_log بسعر محجوز — حتى مع
    _post_to_webhook حقيقياً (لا Mock كامل) يُفلتر عبر requests.post مزيّف."""
    from utils import send_log as sl

    name = "مسك أبيض"
    shadow_db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(shadow_db))
    conn.execute("CREATE TABLE opportunity_scores (norm_name TEXT, price_median REAL)")
    conn.execute("INSERT INTO opportunity_scores VALUES (?,?)", (_normalize_for_store(name), 500.0))
    conn.commit(); conn.close()

    sendlog_db = str(tmp_path / "send_log.db")
    monkeypatch.setattr(sl, "get_data_db_path", lambda *_: sendlog_db)
    sl.init_send_log(sendlog_db)

    class _Resp:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(mh.requests, "post", lambda *a, **k: _Resp())

    out = mh.send_price_updates([
        {"name": name, "product_id": "20", "NO": "20", "price": 100.0},   # 100 < 250 ⇒ محجوز
        {"name": name, "product_id": "21", "NO": "21", "price": 480.0},   # يمرّ
    ])
    assert out["success"] is True

    rows = sqlite3.connect(sendlog_db).execute(
        "SELECT sku, price, success FROM send_log ORDER BY id").fetchall()
    # صفّ الممرَّر بسعره الحقيقي + صفّ المحجوز **بلا سعر** (NULL لا 100.0) وsuccess=0
    assert ("21", 480.0, 1) in rows
    blocked_rows = [r for r in rows if r[0] == "20"]
    assert len(blocked_rows) == 1
    assert blocked_rows[0][1] is None       # لا يُسجَّل السعر المحجوز أبداً
    assert blocked_rows[0][2] == 0          # success=False — لم يُرسَل فعلاً
