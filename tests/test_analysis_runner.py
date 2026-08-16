"""tests/test_analysis_runner.py — تحصين التحليل الخلفي ضد الانقطاع.

الجذر (حادثة 2026-07-12، نسختان): (1) التحليل المتزامن داخل دورة عرض
Streamlit ضاع عرضُ نتائجه (~110 دقيقة حساب) بضغطة زر؛ (2) الإصلاح الأول
(خيط داخل نفس عملية الخادم) أزال هذا لكن أبقى GIL مشتركاً مع خادم الويب،
فخنق خيطٌ حسابي طويل قدرة الخادم على الرد (Connection timed out) رغم أن كل
شيء يعمل. الإصلاح النهائي: عملية نظام تشغيل منفصلة تماماً (subprocess) —
هنا نتحقق من: ذرّية ملف التقدم؛ قفل «تحليل واحد فقط» واحترام النبض الميت؛
تقدير المدة القادمة من تشغيل *بارد* فقط لا من كاش سريع (الجذر الثاني بالضبط)؛
الحفظ الفعلي للقطة مع **دمج قرارات المالك القديمة لا محوها**
(hidden_products/processed_log — #DATA_CONSERVATION)؛ وتدوين مسار الخطأ؛
وأن الإطلاق الحقيقي يستخدم عملية معزولة (subprocess.Popen) لا خيطاً.
لا حسابات ثقيلة: خط التحليل يُزرع وهمياً عبر monkeypatch على bootstrap.
"""
from __future__ import annotations

import json
import time

import pandas as pd
import pytest

import services.analysis_runner as ar
import ui.state_manager as sm
from core.models import AnalysisResult


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    """يعزل ملفات المشغّل واللقطة في مجلد مؤقت (لا لمس لبيانات المشروع)."""
    monkeypatch.setattr(ar, "_PROGRESS_PATH", tmp_path / "progress.json")
    monkeypatch.setattr(ar, "_UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ar, "_LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(sm, "_SNAPSHOT_PATH", tmp_path / "ui_session.json")
    monkeypatch.setattr(ar, "_active_process", None)
    return tmp_path


def _fake_pipeline(monkeypatch, fail_at: str | None = None):
    """يزرع خط تحليل وهمي سريع في bootstrap/الخدمات (يحاكي الواجهات الحقيقية)."""
    import bootstrap as bs
    import services.brand_manager as bm
    import services.catalog_service as cs
    import ui.reconcile as rc

    our = pd.DataFrame({"المنتج": ["أ", "ب"]})
    monkeypatch.setattr(cs, "load_catalog_bytes", lambda data, name: our)
    monkeypatch.setattr(bm, "save_store_brands", lambda df: None)
    monkeypatch.setattr(bs, "populate_our_catalog", lambda df: None)
    monkeypatch.setattr(bs, "build_container", lambda: object())
    missing = pd.DataFrame({"المنتج": ["ج"]})
    monkeypatch.setattr(
        bs, "run_missing_analysis", lambda c, df: (missing, {"rows": 1}),
    )
    sections = {"approved": pd.DataFrame({"المنتج": ["أ"]})}
    result = AnalysisResult(total=2)

    def _pricing(c, df, missing_df=None, memory_callback=None):
        if fail_at == "matching":
            raise RuntimeError("boom-matching")
        if memory_callback is not None:
            memory_callback("fake_pricing", result_rows=1)
        return sections, result, missing, {"catalog_recovered": 0, "cached": True}

    monkeypatch.setattr(bs, "run_pricing_analysis", _pricing)
    monkeypatch.setattr(bs, "run_v4_post_analysis", lambda c, s, r: None)
    monkeypatch.setattr(rc, "reconcile_state", lambda s: None)
    return our, sections


def test_progress_roundtrip_atomic(tmp_paths):
    ar._write_progress({"phase": "matching", "running": True})
    p = ar.read_progress()
    assert p["phase"] == "matching" and p["running"] is True
    assert float(p["updated_at"]) > 0
    # الاستبدال الذرّي اكتمل — لا ملف مؤقت متبقٍ يفسد قراءة لاحقة
    assert not (tmp_paths / "progress.json.tmp").exists()


def test_is_running_respects_stale_heartbeat(tmp_paths):
    ar._write_progress({"running": True})
    assert ar.is_running() is True
    # نبض أقدم من العتبة ⇒ الخيط ميت ⇒ لا يُحسب تشغيلاً (يفتح إعادة المحاولة)
    stale = ar.read_progress()
    stale["updated_at"] = time.time() - ar._STALE_SEC - 5
    (tmp_paths / "progress.json").write_text(
        json.dumps(stale, ensure_ascii=False), encoding="utf-8",
    )
    assert ar.is_running() is False


def test_start_refuses_while_running(tmp_paths):
    ar._write_progress({"running": True})
    ok, msg = ar.start_background_analysis(b"x", "c.xlsx")
    assert ok is False
    assert "يعمل الآن" in msg


def test_job_saves_snapshot_and_preserves_owner_decisions(tmp_paths, monkeypatch):
    """#DATA_CONSERVATION: الحفظ الخلفي يدمج قرارات المالك القديمة ولا يمحوها."""
    _fake_pipeline(monkeypatch)
    # لقطة قديمة تحمل قرارات مالك (منتج مخفي + سجل معالجة) يجب أن تنجو
    old = sm.AppState()
    old.sections = {"approved": pd.DataFrame({"المنتج": ["قديم"]})}
    old.hidden_products = {"منتج مخفي"}
    old.processed_log = [{"x": 1}]
    assert old.persist_results() is True

    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")
    final = ar.run_sync_from_path(str(upload), "c.xlsx")

    assert final["done"] is True and final["error"] is None
    assert final["summary"]["snapshot_saved"] is True
    fresh = sm.AppState()
    assert fresh.restore_results() is True
    assert fresh.hidden_products == {"منتج مخفي"}       # قرارات المالك بقيت
    assert list(fresh.processed_log) == [{"x": 1}]
    assert fresh.sections["approved"]["المنتج"].tolist() == ["أ"]  # النتائج الجديدة حلّت


def test_phase_timings_recorded_for_every_phase(tmp_paths, monkeypatch):
    """ساعة الأطوار: الجولة تقيس نفسها فلا نحتاج جولة 110 دقيقة لنعرف أين الزمن.

    قبل هذا كان المسجَّل زمناً كلياً واحداً، وكل تشخيص للبطء استقراءً من عيّنة.
    """
    _fake_pipeline(monkeypatch)
    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")
    final = ar.run_sync_from_path(str(upload), "c.xlsx")

    timings = final.get("phase_timings")
    assert isinstance(timings, dict) and timings, "لا أزمنة أطوار مسجَّلة"
    # كل طور فعليّ + «boot» (الاستيراد وبناء الحاوية قبل أول طور)
    for phase in ("boot", "loading_catalog", "missing", "matching",
                  "salvage", "post", "saving"):
        assert phase in timings, f"الطور «{phase}» بلا زمن مسجَّل"
        assert timings[phase] >= 0

    # المجموع لا يتجاوز الزمن الكلي (لا ازدواج احتساب)
    assert sum(timings.values()) <= float(final["duration_sec"]) + 1.0


def test_phase_timings_visible_during_run_not_only_at_end(tmp_paths, monkeypatch):
    """تُكتب عند كل انتقال — فلو تعطّلت جولة في منتصفها بقي ما قِيس منها."""
    seen: list = []
    _fake_pipeline(monkeypatch)
    real_pricing = None

    import bootstrap as bs
    real_pricing = bs.run_pricing_analysis

    def _spy(c, df, missing_df=None, memory_callback=None):
        # عند هذه النقطة انتهى طورا loading_catalog وmissing وسُجّلا
        seen.append(dict((ar.read_progress() or {}).get("phase_timings") or {}))
        return real_pricing(c, df, missing_df=missing_df, memory_callback=memory_callback)

    monkeypatch.setattr(bs, "run_pricing_analysis", _spy)
    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")
    ar.run_sync_from_path(str(upload), "c.xlsx")

    assert seen and "loading_catalog" in seen[0], "الأزمنة لا تُكتب إلا في النهاية"
    assert "saving" not in seen[0], "طور لم يبدأ بعد لا يُسجَّل له زمن"


def test_matching_memory_samples_are_visible_during_run(tmp_paths, monkeypatch):
    """قياسات الذاكرة تبقى محدودة ومرئية ولا تغيّر نتيجة الخط الوهمي."""
    _fake_pipeline(monkeypatch)
    monkeypatch.setattr(ar, "_cgroup_memory_bytes", lambda: 123456)
    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")

    final = ar.run_sync_from_path(str(upload), "c.xlsx")

    samples = final.get("matching_memory_samples")
    assert isinstance(samples, list)
    assert [sample["stage"] for sample in samples] == [
        "before_pricing_analysis", "fake_pricing", "after_pricing_analysis",
    ]
    assert all(sample["cgroup_memory_bytes"] == 123456 for sample in samples)
    assert final["summary"]["total"] == 2  # نتيجة الخط لم تتغير


def test_job_error_recorded_and_unlocks(tmp_paths, monkeypatch):
    _fake_pipeline(monkeypatch, fail_at="matching")
    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")
    final = ar.run_sync_from_path(str(upload), "c.xlsx")
    assert final["done"] is True
    assert "boom-matching" in str(final["error"])
    assert ar.is_running() is False  # الفشل يفكّ القفل — لا انسداد دائم


def test_start_background_launches_isolated_process(tmp_paths, monkeypatch):
    """الإصلاح الجذري: الإطلاق يستخدم عملية معزولة (لا خيط) — لا GIL مشترك مع الخادم."""
    captured = {}

    class _FakeProc:
        def poll(self):
            return None  # ما زال "يعمل" من منظور الأب — لا يُشغَّل حقاً هنا

    def _fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(ar.subprocess, "Popen", _fake_popen)
    ok, msg = ar.start_background_analysis(b"bytes", "cat.xlsx")
    assert ok is True
    assert "عملية منفصلة" in msg
    assert (tmp_paths / "uploads" / "last_catalog.xlsx").exists()  # نسخة الرفع محفوظة

    argv = captured["argv"]
    assert argv[0] == ar.sys.executable
    assert argv[1:4] == ["-m", "services.analysis_runner", "--child"]
    assert argv[4] == str(tmp_paths / "uploads" / "last_catalog.xlsx")
    assert argv[5] == "cat.xlsx"
    p = ar.read_progress()
    assert p["running"] is True and p["done"] is False
    assert argv[6] == p["run_id"]


def test_start_background_refuses_when_process_alive(tmp_paths, monkeypatch):
    class _AliveProc:
        def poll(self):
            return None  # لا يزال حياً

    monkeypatch.setattr(ar, "_active_process", _AliveProc())
    ok, msg = ar.start_background_analysis(b"x", "c.xlsx")
    assert ok is False
    assert "يعمل الآن" in msg


def test_child_entrypoint_runs_job_without_reinitializing(tmp_paths, monkeypatch):
    """وضع --child لا يعيد كتابة started_at/estimate الأب — فقط ينفّذ الحساب."""
    _fake_pipeline(monkeypatch)
    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")
    ar._write_progress({
        "run_id": "run_123", "running": True, "done": False,
        "started_at": 111.0, "estimate_sec": 42.0,
    })
    ar._analysis_job(str(upload), "c.xlsx", "run_123")
    p = ar.read_progress()
    assert p["started_at"] == 111.0          # لم يُعَد تهيئتها
    assert p["done"] is True and p["error"] is None


def test_next_estimate_uses_cold_run_not_cached_run(tmp_paths, monkeypatch):
    """الجذر الثاني للحادثة: كاش سريع لا يصلح تقديراً لتشغيل بارد لاحق."""
    import bootstrap as bs

    our = pd.DataFrame({"المنتج": ["أ"]})
    monkeypatch.setattr("services.catalog_service.load_catalog_bytes", lambda d, n: our)
    monkeypatch.setattr("services.brand_manager.save_store_brands", lambda df: None)
    monkeypatch.setattr(bs, "populate_our_catalog", lambda df: None)
    monkeypatch.setattr(bs, "build_container", lambda: object())
    monkeypatch.setattr(bs, "run_missing_analysis", lambda c, df: (pd.DataFrame(), {}))
    monkeypatch.setattr(bs, "run_v4_post_analysis", lambda c, s, r: None)
    monkeypatch.setattr("ui.reconcile.reconcile_state", lambda s: None)

    def _cached_fast(c, df, missing_df=None, memory_callback=None):
        if memory_callback is not None:
            memory_callback("pricing_cache_hit", result_rows=1)
        return {}, AnalysisResult(total=1), pd.DataFrame(), {"cached": True}

    monkeypatch.setattr(bs, "run_pricing_analysis", _cached_fast)
    upload = tmp_paths / "up.xlsx"
    upload.write_bytes(b"fake")
    ar.run_sync_from_path(str(upload), "c.xlsx")

    # التشغيل السابق كان مخدوماً بالكاش (سريع) ⇒ لا يجب أن يحدّث cold_duration_sec
    prog = ar.read_progress()
    assert prog["summary"]["cached_pricing"] is True
    assert prog.get("cold_duration_sec") is None

    # لا نطلق عملية حقيقية هنا — الاختبار يفحص فقط منطق التقدير
    monkeypatch.setattr(
        ar.subprocess, "Popen",
        lambda *a, **k: type("P", (), {"poll": lambda self: None})(),
    )
    ok, _ = ar.start_background_analysis(b"y", "c2.xlsx")
    assert ok is True
    # بلا تشغيل بارد سابق ⇒ التقدير الافتراضي، وليس مدة التشغيل السريع المخدوم بالكاش
    assert ar.read_progress()["estimate_sec"] == ar._DEFAULT_ESTIMATE_SEC


def test_elapsed_and_estimate_sane(tmp_paths):
    ar._write_progress({"started_at": time.time() - 120, "estimate_sec": 600})
    elapsed, estimate = ar.elapsed_and_estimate(ar.read_progress())
    assert 115 <= elapsed <= 130
    assert estimate == 600
