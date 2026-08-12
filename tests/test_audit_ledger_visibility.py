# -*- coding: utf-8 -*-
"""عدّادات التدقيق يجب أن تصل المالك لا أن تُحسَب وتُرمى.

الجذر (جرد 2026-07-25): ``audit_stats`` كان سجلّاً شبه كامل الكتابة بلا قراءة —
``closed_loop`` و``closed_loop_error`` و``dropped_none`` لكلٍّ منها مواضع كتابة
وصفر مواضع قراءة. أي أن النظام يَعُدّ خسائره في البيانات ثم لا يخبر أحداً:

* ``closed_loop`` — ناتج «الإقفال الكمومي» الذي يتحقّق أن لا منتج منافس تسرّب.
* ``closed_loop_error`` — انهيار كاشف التسرّب نفسه.
* ``dropped_none`` — عدد الصفوف المُسقَطة فعلاً أثناء المطابقة.

ولافتة اكتمال التحليل كانت **غير متماثلة**: تُعلن سلامة حفظ البيانات وتصمت عن
خلله، فتتطابق اللافتة الخضراء في الحالتين. الصمت عن الخلل أسوأ من عدم الفحص
لأنه يوحي بالسلامة.

اختبارات اللافتة **سلوكية**: تستدعي ``_render_analysis_outcome`` فعلياً وتلتقط
ما عُرض. فحص وجود نصّ في المصدر حارس ضعيف — يمرّ على كود لا يعمل.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_AUDIT_KEYS = ("closed_loop", "closed_loop_error", "dropped_none")


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── جسر التحليل → اللوحة (فحص وصل: مفتاح غائب من الملخّص = مفتاح ميت) ──
@pytest.mark.parametrize("key", _AUDIT_KEYS)
def test_runner_summary_carries_audit_key(key):
    src = _source("services/analysis_runner.py")
    start = src.index("summary = {")
    end = src.index("total_dur = round(", start)
    assert key in src[start:end], f"{key} لا يُمرَّر في summary — لن يصل لوحة التحكم"


def test_bootstrap_logs_closed_loop_problems():
    """تسجيل مستقل عن الواجهة: لو لم تُقرأ اللوحة يبقى أثر في السجلّ."""
    src = _source("bootstrap.py")
    start = src.index('audit_stats["closed_loop"]')
    assert "logger.warning" in src[start:start + 1600], (
        "فشل/اختلال الإقفال الكمومي لا يترك أثراً في السجلّ"
    )


# ── سلوك اللافتة الفعلي ───────────────────────────────────────────────
class _Recorder:
    """يلتقط ما تعرضه الدالة بدل رسمه (بديل streamlit في الاختبار)."""

    def __init__(self):
        self.success, self.error, self.warning = [], [], []
        self.session_state = {}

    def button(self, *_a, **_k):
        return False           # لا ضغط ⇒ لا تُنفَّذ فروع التحميل

    def rerun(self):           # pragma: no cover — لا يُبلَغ في هذه الحالات
        raise AssertionError("rerun غير متوقّع")


@pytest.fixture()
def render(monkeypatch):
    """يستدعي اللافتة بملخّص مُعطى ويُرجع ما عُرض."""
    import time

    import streamlit
    from ui.pages.dashboard import _render_analysis_outcome

    def _run(summary: dict) -> _Recorder:
        rec = _Recorder()
        for name in ("success", "error", "warning"):
            monkeypatch.setattr(
                streamlit, name,
                lambda msg, *a, _bucket=getattr(rec, name), **k: _bucket.append(str(msg)),
            )
        monkeypatch.setattr(streamlit, "button", rec.button)
        monkeypatch.setattr(streamlit, "rerun", rec.rerun)
        monkeypatch.setattr(streamlit, "session_state", rec.session_state)
        _render_analysis_outcome(None, {
            "run_id": "run_test", "finished_at": time.time(),
            "duration_sec": 600, "summary": summary,
        })
        return rec

    return _run


def test_balanced_true_reports_sound_and_no_error(render):
    rec = render({"balanced": True})
    assert any("سليم" in m for m in rec.success)
    assert rec.error == []


def test_balanced_false_raises_a_visible_error(render):
    """الحارس الأهم: الخلل كان يمرّ بلافتة خضراء متطابقة."""
    rec = render({"balanced": False, "gap": 7, "duplicates": 2})
    assert any("خلل في حفظ البيانات" in m for m in rec.error), (
        "اختلال حفظ البيانات لم يُعرض — عاد الصمت"
    )
    assert not any("سليم" in m for m in rec.success)
    joined = " ".join(rec.error)
    assert "7" in joined and "2" in joined     # الفجوة والتكرار مذكورتان بالرقم


def test_closed_loop_error_is_surfaced(render):
    rec = render({"balanced": True, "closed_loop_error": "قاعدة مقفلة"})
    assert any("تسرّب" in m and "قاعدة مقفلة" in m for m in rec.warning)


def test_dropped_none_does_not_raise_a_false_alarm(render):
    """`dropped_none` **لا يعني ضياعاً**: المحرّك يُلحق لكلٍّ منها صفّاً مستبعداً
    مرئياً يغلق التوازن (engine.py:2903 و3117). عرضها كخسارة إنذار كاذب على
    متجر حيّ — وقد وقعتُ فيه فعلاً قبل قراءة سياق العدّاد."""
    rec = render({"balanced": True, "dropped_none": 12})
    assert rec.warning == [], "dropped_none أطلق تحذيراً — عاد الإنذار الكاذب"
    assert rec.error == []


def test_salvaged_count_appears_in_banner(render):
    rec = render({"balanced": True, "deterministic_salvaged": 233})
    assert any("233" in m and "مُنقَذ" in m for m in rec.success)


def test_unknown_balance_is_neither_sound_nor_failure(render):
    """ملخّصات أقدم بلا مفتاح balanced: مجهول ≠ فاشل — لا ادّعاء سلامة ولا إنذار."""
    rec = render({})
    assert rec.error == []
    assert not any("سليم" in m for m in rec.success)
    assert len(rec.success) == 1        # اللافتة نفسها تظهر


def test_clean_run_shows_no_warnings_at_all(render):
    """حارس ضد الإفراط: تحليل سليم لا يُغرق المالك بتحذيرات."""
    rec = render({"balanced": True})
    assert rec.warning == [] and rec.error == []
