"""tests/test_enrich_trace.py — الصندوق الزجاجي: الإثراء يصرّح لماذا غاب كل حقل.

_build_enrich_trace دالة بيان خالصة: تفحص ناتج خط الإثراء وتُرجع {field: reason}
للحقول الناقصة فقط (image/notes/brand/desc)؛ النجاح الكامل ⇒ dict فارغ. لا تغيّر
منطق الإثراء ولا is_enriched. _gate_reason_detail يدمجها مع سبب البوابة.
"""
from services.enrichment_service import (
    GATE_REASON_BRAND,
    GATE_REASON_IMAGE,
    GATE_REASON_PRICE,
)
from services.missing_orchestrator import (
    MissingProductsOrchestrator,
    _build_enrich_trace,
    _gate_reason_detail,
)

_GOOD_DESC = (
    "عطر مهووس فاخر — الهرم العطري: مقدمة العطر وقلب العطر وقاعدة العطر "
    "بنفحات راقية تدوم طويلاً على البشرة طوال اليوم."
)


# ── _build_enrich_trace ──────────────────────────────────────────────────

def test_failed_source_traces_image_and_notes_with_error():
    trace = _build_enrich_trace(
        {"اسم المنتج": "عطر مجهول"},
        {"success": False, "_error": "timeout"},
        enrich_ai=True,
    )
    assert trace["image"] == "فشل جلب المصدر: timeout"
    assert trace["notes"] == "فشل جلب المصدر: timeout"
    assert trace["brand"]  # سبب الماركة موجود
    assert trace["desc"]   # سبب الوصف موجود


def test_ai_disabled_reason():
    trace = _build_enrich_trace({"اسم المنتج": "عطر"}, {}, enrich_ai=False)
    assert trace["image"] == "الإثراء AI معطّل"
    assert trace["notes"] == "الإثراء AI معطّل"


def test_source_succeeded_but_returned_nothing():
    trace = _build_enrich_trace({"اسم المنتج": "عطر"}, {"success": True}, enrich_ai=True)
    assert trace["image"] == "المصدر لم يُرجع صورة"
    assert trace["notes"] == "المصدر لم يُرجع نوتات"


def test_missing_brand_reason():
    trace = _build_enrich_trace(
        {"صورة_المنافس": "https://x/y.jpg", "top_notes": "برغموت", "الوصف": _GOOD_DESC},
        {"success": True},
        enrich_ai=True,
    )
    assert set(trace.keys()) == {"brand"}
    assert "الماركة" in trace["brand"]


def test_missing_description_reason():
    trace = _build_enrich_trace(
        {"صورة_المنافس": "https://x/y.jpg", "top_notes": "برغموت", "الماركة": "ديور", "الوصف": "قصير"},
        {"success": True},
        enrich_ai=True,
    )
    assert set(trace.keys()) == {"desc"}
    assert trace["desc"]


def test_complete_enrichment_has_no_trace():
    trace = _build_enrich_trace(
        {
            "صورة_المنافس": "https://cdn.example.com/x.jpg",
            "top_notes": "برغموت",
            "الماركة": "ديور",
            "الوصف": _GOOD_DESC,
        },
        {"success": True},
        enrich_ai=True,
    )
    assert trace == {}


# ── _gate_reason_detail (دمج سبب البوابة ← تتبّع الإثراء) ─────────────────

def test_gate_reason_detail_joins_reason_and_trace():
    p = {"_gate_reason": GATE_REASON_IMAGE, "_enrich_trace": {"image": "المصدر لم يُرجع صورة"}}
    assert _gate_reason_detail(p) == f"{GATE_REASON_IMAGE} ← المصدر لم يُرجع صورة"


def test_gate_reason_detail_without_trace_returns_reason_only():
    assert _gate_reason_detail({"_gate_reason": GATE_REASON_PRICE}) == GATE_REASON_PRICE


# ── تكامل: _enrich_and_format يخزّن _enrich_trace (والنجاح لا) ────────────

def test_enrich_and_format_stores_trace_on_failure(monkeypatch):
    orch = MissingProductsOrchestrator(enrich_with_ai=True)
    # عزل المتعاونين لجعل الإثراء يُرجع فراغاً حتمياً.
    monkeypatch.setattr(orch, "_fetch_fragrantica_safe", lambda name: {"success": False, "_error": "no-src"})
    monkeypatch.setattr(orch, "_resolve_brand", lambda product, pname: "")
    monkeypatch.setattr(orch, "_resolve_category", lambda product: "")
    monkeypatch.setattr(orch, "_ensure_golden_description", lambda product, frag: None)
    monkeypatch.setattr(orch, "_ensure_sku", lambda product: None)

    out = orch._enrich_and_format({"اسم المنتج": "عطر تجريبي", "سعر_المنافس": 120})

    assert "_enrich_trace" in out
    assert out["_enrich_trace"]["image"] == "فشل جلب المصدر: no-src"
    assert out["_enrich_trace"]["brand"]


def test_enrich_and_format_no_trace_on_success(monkeypatch):
    orch = MissingProductsOrchestrator(enrich_with_ai=True)
    monkeypatch.setattr(orch, "_fetch_fragrantica_safe", lambda name: {"success": True})
    monkeypatch.setattr(orch, "_resolve_brand", lambda product, pname: "ديور")
    monkeypatch.setattr(orch, "_resolve_category", lambda product: "")
    monkeypatch.setattr(orch, "_ensure_sku", lambda product: None)

    def _set_desc_and_image(product, frag):
        product["الوصف"] = _GOOD_DESC
        product["صورة_المنافس"] = "https://cdn.example.com/x.jpg"
        product["top_notes"] = "برغموت"

    monkeypatch.setattr(orch, "_ensure_golden_description", _set_desc_and_image)

    out = orch._enrich_and_format({"اسم المنتج": "عطر تجريبي", "سعر_المنافس": 120})

    assert "_enrich_trace" not in out
