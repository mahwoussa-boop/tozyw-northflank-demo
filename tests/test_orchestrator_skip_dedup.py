"""tests/test_orchestrator_skip_dedup.py — تجاوز بوابة منع التكرار «رغم التشابه».

يثبّت إصلاح «ارسلت المنتج لم يصل»: منتج تحجبه بوابة التكرار يُرجع success=True مع
sent=0 (لا وصول فعلي)؛ و``skip_dedup=True`` (قرار المستخدم) يتخطّى البوابة فيصل.
"""
from types import SimpleNamespace

from services.missing_orchestrator import MissingProductsOrchestrator

_PRODUCT = {
    "منتج_المنافس": "بربري هيرو الكسير دي بارفيوم 60 مل",
    "سعر_المنافس": 300,
    "الماركة": "بربري Burberry",
    "الوصف": "<p>وصف مهووس…</p>",
    "صورة_المنافس": "https://cdn.example.com/hero.jpg",
}


def _patch(monkeypatch, *, drop_all: bool):
    """يحقن send_batch_smart (يلتقط) + screen_for_duplicates (يُسقط الكل/يمرّر)."""
    import utils.make_helper as mh
    import utils.missing_queue_manager as mq

    captured = {"n": None}

    def fake_send(products, **kw):
        captured["n"] = len(products)
        return {"success": True, "sent": len(products), "failed": 0, "message": "ok"}

    def fake_screen(products, catalog, **kw):
        if drop_all:
            return SimpleNamespace(
                kept=[], summary={"new": 0, "likely": 0, "match": len(products)})
        return SimpleNamespace(
            kept=list(products),
            summary={"new": len(products), "likely": 0, "match": 0})

    monkeypatch.setattr(mh, "send_batch_smart", fake_send)
    monkeypatch.setattr(mq, "screen_for_duplicates", fake_screen)
    return captured


def test_dedup_holds_product_no_send(monkeypatch) -> None:
    captured = _patch(monkeypatch, drop_all=True)
    res = MissingProductsOrchestrator(skip_dedup=False)._export_make(
        [dict(_PRODUCT)], "dataframe")
    assert res["success"] is True       # البوابة لا تُعدّ خطأً
    assert res["sent"] == 0             # لكن لا وصول فعلي ← الواجهة لا تدّعي النجاح
    assert captured["n"] is None        # لم يُستدعَ الإرسال أصلاً


def test_skip_dedup_force_sends(monkeypatch) -> None:
    captured = _patch(monkeypatch, drop_all=True)   # البوابة تُسقط لو استُدعيت
    res = MissingProductsOrchestrator(skip_dedup=True)._export_make(
        [dict(_PRODUCT)], "dataframe")
    assert res["success"] is True
    assert res["sent"] == 1             # وصل رغم التشابه
    assert captured["n"] == 1           # تخطّى البوابة ووصل للإرسال


def test_normal_send_when_not_duplicate(monkeypatch) -> None:
    captured = _patch(monkeypatch, drop_all=False)
    res = MissingProductsOrchestrator(skip_dedup=False)._export_make(
        [dict(_PRODUCT)], "dataframe")
    assert res["success"] is True and res["sent"] == 1
    assert captured["n"] == 1
