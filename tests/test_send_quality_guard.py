"""م4 — بوّابة أهلية الإرسال: ثقة · توفّر · حداثة.

الحارسان القائمان (أرضية 50% ونطاق ±20%) يحرسان **السعر**؛ هذا يحرس **جودة
الدليل**: مطابقة ضعيفة، أو سوق كل عروضه نافدة، أو بيانات قديمة.

اختبار التكامل الحاسم: صفٌّ ضعيف **لا يستدعي Make إطلاقاً**، والمؤهَّل يستدعيه
**مرّة واحدة** — يُقاس بعدّاد ناشر محقون، لا بادّعاء.
"""
from __future__ import annotations

import sqlite3

import pytest

from services import send_quality_guard as guard
from services.send_quality_guard import split_low_quality


def _market_db(tmp_path, rows):
    """قاعدة سوق مصغّرة بنفس مخطّط opportunity_scores الذي يقرأه الحارس."""
    db = tmp_path / "pricing_v18.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE opportunity_scores (norm_name TEXT, instock_price_min REAL,"
        " data_age_days REAL, price_median REAL)"
    )
    con.executemany(
        "INSERT INTO opportunity_scores VALUES (?,?,?,?)", rows,
    )
    con.commit()
    con.close()
    return str(db)


def _item(**kw):
    base = {"NO": "1", "product_id": "1", "name": "عطر تجريبي",
            "comp_name": "عطر تجريبي", "price": 300.0, "section": "raise",
            "match_score": 95.0}
    base.update(kw)
    return base


@pytest.fixture
def _norm(monkeypatch):
    """تطبيع مطابق بين الكتابة والقراءة — نعزل الاختبار عن سلوك normalize."""
    import utils.db_manager as dbm
    monkeypatch.setattr(dbm, "_normalize_for_store", lambda s: str(s).strip())


def test_low_confidence_is_held(tmp_path, _norm):
    db = _market_db(tmp_path, [("عطر تجريبي", 250.0, 1.0, 300.0)])
    allowed, held = split_low_quality([_item(match_score=61.0)], db_path=db)
    assert allowed == []
    assert len(held) == 1
    assert "61%" in held[0]["blocked_reason"]


def test_market_with_no_in_stock_offer_is_held(tmp_path, _norm):
    """instock_price_min = NULL ⇒ كل العروض نافدة ⇒ لا سعر يُقارَن به."""
    db = _market_db(tmp_path, [("عطر تجريبي", None, 1.0, 300.0)])
    allowed, held = split_low_quality([_item()], db_path=db)
    assert allowed == []
    assert "نافدة" in held[0]["blocked_reason"]


def test_stale_market_data_is_held(tmp_path, _norm):
    db = _market_db(tmp_path, [("عطر تجريبي", 250.0, 42.0, 300.0)])
    allowed, held = split_low_quality([_item()], db_path=db)
    assert allowed == []
    assert "42" in held[0]["blocked_reason"]


def test_healthy_row_passes(tmp_path, _norm):
    db = _market_db(tmp_path, [("عطر تجريبي", 250.0, 1.0, 300.0)])
    allowed, held = split_low_quality([_item()], db_path=db)
    assert held == []
    assert len(allowed) == 1


def test_missing_market_row_passes(tmp_path, _norm):
    """لا يقين ⇒ لا حجب (نفس مبدأ الحارسين القائمين)."""
    db = _market_db(tmp_path, [])
    allowed, held = split_low_quality([_item()], db_path=db)
    assert held == [] and len(allowed) == 1


def test_unenforced_section_is_untouched(tmp_path, _norm):
    db = _market_db(tmp_path, [("عطر تجريبي", None, 99.0, 300.0)])
    allowed, held = split_low_quality(
        [_item(section="missing", match_score=10.0)], db_path=db)
    assert held == [] and len(allowed) == 1


def test_guard_is_fail_open_on_broken_db(_norm):
    allowed, held = split_low_quality([_item()], db_path="/no/such/db.sqlite")
    assert held == [] and len(allowed) == 1


def test_emptying_enforced_sections_disables_the_gate(tmp_path, monkeypatch, _norm):
    """مسار تراجع فوري بلا حذف كود."""
    db = _market_db(tmp_path, [("عطر تجريبي", None, 99.0, 300.0)])
    monkeypatch.setattr(guard, "ENFORCED_SECTIONS", set())
    allowed, held = split_low_quality([_item(match_score=1.0)], db_path=db)
    assert held == [] and len(allowed) == 1


def test_every_held_item_carries_a_visible_reason(tmp_path, _norm):
    """لا رفض صامت — شرط معلن في الحارسين الشقيقين."""
    db = _market_db(tmp_path, [("عطر تجريبي", None, 99.0, 300.0)])
    _allowed, held = split_low_quality(
        [_item(match_score=10.0), _item(name="آخر", comp_name="آخر")], db_path=db)
    assert held, "لا شيء حُجز — الاختبار لا يحرس شيئاً"
    for row in held:
        assert str(row.get("blocked_reason", "")).strip()


# ── اختبار التكامل: هل يصل الصفّ الضعيف إلى Make فعلاً؟ ──────────────────────
class _CountingPoster:
    """ناشر محقون يعدّ الاستدعاءات — الدليل الوحيد المقبول على «لم يُرسَل»."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)

        class _R:
            status_code = 200
            text = '{"ok":true}'
        return _R()


_HOOK = "https://hook.test/x"


def _export_service(tmp_path, monkeypatch, poster, rows):
    from services.export_service import ExportService

    db = _market_db(tmp_path, rows)
    monkeypatch.setattr("services.pricing_shadow._default_db", lambda: db)
    return ExportService(poster=poster)


def test_low_quality_row_never_reaches_make(tmp_path, monkeypatch, _norm):
    poster = _CountingPoster()
    svc = _export_service(tmp_path, monkeypatch, poster,
                          [("عطر تجريبي", None, 1.0, 300.0)])
    result = svc.post_to_make(_HOOK, [_item()], envelope="products")

    assert poster.calls == [], "عنصر غير مؤهَّل وصل إلى Make — البوّابة لا تعمل"
    assert result["state"] == "held_low_quality"
    assert len(result["held_low_quality"]) == 1


def test_eligible_row_reaches_make_exactly_once(tmp_path, monkeypatch, _norm):
    poster = _CountingPoster()
    svc = _export_service(tmp_path, monkeypatch, poster,
                          [("عطر تجريبي", 250.0, 1.0, 300.0)])
    svc.post_to_make(_HOOK, [_item()], envelope="products")

    assert len(poster.calls) == 1, "المؤهَّل لم يُرسَل مرّة واحدة بالضبط"
    sent = poster.calls[0]["products"]
    assert len(sent) == 1 and sent[0]["name"] == "عطر تجريبي"


class _HoldingService:
    """خدمة تحجز كل شيء — لقياس تعامل مُرسِل الدفعات مع المحجوز."""

    def __init__(self):
        self.calls = 0

    def post_to_make(self, url, chunk, envelope="products"):
        self.calls += 1
        return {
            "success": True, "confirmed": False, "state": "held_low_quality",
            "held_low_quality": [{**c, "blocked_reason": "ثقة منخفضة"} for c in chunk],
        }


def test_batches_do_not_count_held_rows_as_failures_or_retry_them():
    """الحجب ليس فشلاً: لا يُبلَّغ المالك بفشل كاذب، ولا تُهدر ثلاث محاولات."""
    from ui.components.action_bar import send_in_batches

    svc = _HoldingService()
    result = send_in_batches(
        svc, _HOOK,
        [{"name": f"م{i}", "price": 10.0, "section": "raise"} for i in range(3)],
        batch_size=3,
    )
    assert svc.calls == 1, "أُعيدت المحاولة على محجوز — هدر وضجيج"
    assert result["failed"] == 0, "الحجب حُسب فشلاً — تقرير كاذب للمالك"
    assert len(result["held_low_quality"]) == 3
    assert result["sent"] == 0
