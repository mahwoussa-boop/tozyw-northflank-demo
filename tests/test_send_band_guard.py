# -*- coding: utf-8 -*-
"""tests/test_send_band_guard.py — سياج نطاق v1 على مسار الإرسال (وحدة أ4، مرحلة 1).

يثبّت:
- ``split_outside_band`` الخالصة: يحجز below_band/above_band **لقسمَي "raise" و"lower"
  المُفعَّلَين فقط**، يمرّر ok/no_market_row/stale_market دوماً (لا حجب بلا يقين سعري)،
  يتجاهل الأقسام الأخرى كلياً بغضّ النظر عن السعر، fail-open عند خطأ قاعدة.
- ``ENFORCED_SECTIONS = set()`` يعطّل الحجب فوراً (مسار التراجع الموثَّق في الخطة).
- العقفان يطبّقان الحجب فعلياً: ``export_service.post_to_make`` و
  ``make_helper._post_to_webhook`` — لا POST لعنصر محجوز، والحمولة المتبقية سليمة.
"""
import sqlite3

import pytest

import services.send_band_guard as sbg
import utils.make_helper as mh
import utils.send_log as _sl
from services.export_service import ExportService
from services.send_band_guard import split_outside_band
from utils.db_manager import _normalize_for_store


@pytest.fixture(autouse=True)
def _isolate_send_log(monkeypatch, tmp_path):
    """يعزل ``send_log`` إلى قاعدة مؤقتة لكل اختبار في هذا الملف.

    اختبارات العقفين تستدعي ``log_send`` بلا توجيه القاعدة، فكانت تكتب **صفّين
    وهميّين في ``pricing_v18.db`` الحيّ مع كل تشغيل للبوّابة** («عطر شانيل»/210
    و«عطر قسم آخر»/999) — أي مع كل commit، لأن خطّاف pre-commit يشغّل البوّابة.
    قِيس حيّاً 2026-07-26: 1,655 ⇒ 1,657 من تشغيل هذا الملف وحده.
    نفس عزل ``tests/test_export_service.py:_isolate_send_log``."""
    db = str(tmp_path / "sendlog.db")
    monkeypatch.setattr(_sl, "get_data_db_path", lambda *_a, **_k: db)
    _sl.init_send_log(db)


def _mk_db(tmp_path, rows, filename="market.db"):
    """قاعدة مصغّرة (norm_name, price_median, stale_reason) — كل ما يقرأه الحارس."""
    db = str(tmp_path / filename)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE opportunity_scores"
        " (norm_name TEXT, price_median REAL, stale_reason TEXT)"
    )
    conn.executemany(
        "INSERT INTO opportunity_scores VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


# ── الدالة الخالصة ───────────────────────────────────────────────────────────
def test_split_outside_band_holds_below_band_for_enforced_section(tmp_path):
    name = "عطر ديور سوفاج 100 مل"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 100.0, None)])
    allowed, held = split_outside_band(
        [{"name": name, "NO": "1", "price": 70.0, "section": "raise"},   # <80 ⇒ محجوز
         {"name": name, "NO": "2", "price": 90.0, "section": "raise"}],  # داخل النطاق ⇒ يمرّ
        db_path=db,
    )
    assert [a["NO"] for a in allowed] == ["2"]
    assert len(held) == 1 and held[0]["NO"] == "1"
    assert "blocked_reason" in held[0] and "نطاق سياج v1" in held[0]["blocked_reason"]


def test_split_outside_band_holds_above_band_for_enforced_section(tmp_path):
    name = "عطر فرزاتشي"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 100.0, None)])
    allowed, held = split_outside_band(
        [{"name": name, "NO": "1", "price": 130.0, "section": "raise"}],  # >120 ⇒ محجوز
        db_path=db,
    )
    assert allowed == []
    assert len(held) == 1 and held[0]["NO"] == "1"


def test_split_outside_band_ignores_other_sections(tmp_path):
    # نفس السعر الكارثي لكن في قسم غير مفعَّل (raise/lower فقط مُفعَّلان) ⇒ يمرّ دوماً.
    name = "عطر خارج القسم المفعَّل"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 100.0, None)])
    allowed, held = split_outside_band(
        [{"name": name, "NO": "1", "price": 500.0, "section": "approved"},
         {"name": name, "NO": "2", "price": 500.0, "section": "review"},
         {"name": name, "NO": "3", "price": 500.0}],  # بلا قسم إطلاقاً
        db_path=db,
    )
    assert [a["NO"] for a in allowed] == ["1", "2", "3"]
    assert held == []


def test_split_outside_band_holds_below_band_for_lower_section(tmp_path):
    """مرحلة 2 (2026-07-22): قسم "سعر أقل" مُفعَّل الآن بنفس حماية "سعر أعلى"."""
    name = "عطر جفنشي"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 100.0, None)])
    allowed, held = split_outside_band(
        [{"name": name, "NO": "1", "price": 70.0, "section": "lower"},   # <80 ⇒ محجوز
         {"name": name, "NO": "2", "price": 90.0, "section": "lower"}],  # داخل النطاق ⇒ يمرّ
        db_path=db,
    )
    assert [a["NO"] for a in allowed] == ["2"]
    assert len(held) == 1 and held[0]["NO"] == "1"


def test_split_outside_band_no_market_row_never_holds(tmp_path):
    db = _mk_db(tmp_path, [])  # لا صفوف سوق إطلاقاً
    allowed, held = split_outside_band(
        [{"name": "منتج مجهول تماماً", "price": 999.0, "section": "raise"}], db_path=db)
    assert len(allowed) == 1 and held == []


def test_split_outside_band_stale_market_never_holds(tmp_path):
    name = "عطر ببيانات قديمة"
    nn = _normalize_for_store(name)
    db = _mk_db(tmp_path, [(nn, 100.0, "stale")])
    allowed, held = split_outside_band(
        [{"name": name, "price": 999.0, "section": "raise"}], db_path=db)  # خارج النطاق لكن قديم
    assert len(allowed) == 1 and held == []


def test_split_outside_band_skips_garbage_items(tmp_path):
    db = _mk_db(tmp_path, [])
    allowed, held = split_outside_band(
        [{"name": "", "price": 10, "section": "raise"},
         {"name": "بلا سعر", "section": "raise"},
         "ليس قاموساً", None],
        db_path=db,
    )
    assert len(allowed) == 4 and held == []


def test_split_outside_band_fail_open_on_missing_db(tmp_path):
    allowed, held = split_outside_band(
        [{"name": "س", "price": 1.0, "section": "raise"}],
        db_path=str(tmp_path / "missing.db"))
    assert len(allowed) == 1 and held == []


def test_split_outside_band_empty_enforced_sections_disables_guard(tmp_path, monkeypatch):
    name = "عطر أ"
    db = _mk_db(tmp_path, [(_normalize_for_store(name), 100.0, None)])
    monkeypatch.setattr(sbg, "ENFORCED_SECTIONS", set())
    allowed, held = split_outside_band(
        [{"name": name, "price": 999.0, "section": "raise"}], db_path=db)  # كارثي لكن الحارس معطَّل
    assert len(allowed) == 1 and held == []


# ── عقف export_service.post_to_make ─────────────────────────────────────────
def test_post_to_make_holds_outside_band_item(tmp_path):
    """يعتمد على ``_shadow_db_isolated`` (autouse) الذي يوجّه ``_default_db``
    لنفس ``tmp_path`` — إنشاء الملف هنا يجعل الحارس يقرأ سوقاً حقيقياً."""
    name = "عطر شانيل"
    db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE opportunity_scores"
        " (norm_name TEXT, price_median REAL, stale_reason TEXT)")
    conn.execute(
        "INSERT INTO opportunity_scores VALUES (?,?,?)",
        (_normalize_for_store(name), 200.0, None))
    conn.commit(); conn.close()

    captured = {}

    def poster(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        class _Resp:
            status_code = 200
        return _Resp()

    svc = ExportService(poster=poster)
    result = svc.post_to_make("http://hook", [
        {"NO": "1", "name": name, "price": 260.0, "section": "raise"},  # >240 (120%×200) ⇒ محجوز
        {"NO": "2", "name": name, "price": 210.0, "section": "raise"},  # يمرّ
    ])
    assert result["success"] is True
    assert captured["payload"] == {
        "products": [{"NO": "2", "name": name, "price": 210.0, "section": "raise"}]}
    assert len(result["held_outside_band"]) == 1
    assert result["held_outside_band"][0]["NO"] == "1"


def test_post_to_make_other_section_never_held(tmp_path):
    name = "عطر قسم آخر"
    db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE opportunity_scores"
        " (norm_name TEXT, price_median REAL, stale_reason TEXT)")
    conn.execute(
        "INSERT INTO opportunity_scores VALUES (?,?,?)",
        (_normalize_for_store(name), 100.0, None))
    conn.commit(); conn.close()

    captured = {}

    def poster(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        class _Resp:
            status_code = 200
        return _Resp()

    svc = ExportService(poster=poster)
    result = svc.post_to_make("http://hook", [
        {"NO": "1", "name": name, "price": 999.0, "section": "approved"},  # خارج النطاق لكن قسم غير مفعَّل
    ])
    assert result["success"] is True
    assert "held_outside_band" not in result
    assert captured["payload"]["products"][0]["NO"] == "1"


# ── عقف make_helper._post_to_webhook ────────────────────────────────────────
def test_post_to_webhook_filters_band_before_post(tmp_path, monkeypatch):
    name = "عود كمبودي"
    db = tmp_path / "shadow_isolated.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE opportunity_scores"
        " (norm_name TEXT, price_median REAL, stale_reason TEXT)")
    conn.execute(
        "INSERT INTO opportunity_scores VALUES (?,?,?)",
        (_normalize_for_store(name), 400.0, None))
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
        {"NO": "9", "name": name, "price": 520.0, "section": "raise"},   # >480 ⇒ محجوز
        {"NO": "10", "name": name, "price": 420.0, "section": "raise"},  # يمرّ
    ]}
    result = mh._post_to_webhook("http://hook", payload)
    assert result["success"] is True
    assert captured["json"]["products"] == [
        {"NO": "10", "name": name, "price": 420.0, "section": "raise"}]
    assert len(result["held_outside_band"]) == 1 and result["held_outside_band"][0]["NO"] == "9"
