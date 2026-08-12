# -*- coding: utf-8 -*-
"""تكافؤ مسارَي التحليل في استدعاء الإنقاذ الحتمي.

الجذر (2026-07-25): للتحليل مساران — المتزامن في ``app.py`` والمشغّل الخلفي
``services/analysis_runner.py`` (وهو المسار الفعلي لكل تحليل طويل، عملية نظام
منفصلة). الإنقاذ الحتمي كان مستدعىً في الأول **فقط**، فكل نتيجة يحفظها المشغّل
الخلفي تُخزَّن بلا إنقاذ وتبقى مستبعدات لها نظير قوي مكانها.

قياس حيّ على نتائج آخر تحليل: **233 منتجاً** بقيت مستبعدة بسبب هذه الفجوة وحدها.

الاختبار على مستوى الوصل (المصدر) عمداً: العطب لم يكن خطأ في منطق دالة بل
**غياب استدعاء**، وتشغيل ``_analysis_job`` فعلياً يعني تحليلاً كامل الكلفة.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SALVAGE_CALL = "apply_deterministic_salvage"


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["app.py", "services/analysis_runner.py"])
def test_both_analysis_paths_invoke_deterministic_salvage(path):
    """أي مسار تحليل لا يستدعي الإنقاذ يترك مستبعدات قابلة للإنقاذ في مكانها."""
    assert _SALVAGE_CALL in _source(path), (
        f"{path} لا يستدعي الإنقاذ الحتمي — نتائجه ستُحفَظ بمستبعدات قابلة للإنقاذ"
    )


@pytest.mark.parametrize("path", ["app.py", "services/analysis_runner.py"])
def test_salvage_outcome_is_recorded_not_swallowed(path):
    """نجاح الإنقاذ وفشله يجب أن يتركا أثراً — لا ابتلاع صامت في except فارغ."""
    src = _source(path)
    assert "deterministic_salvaged" in src, f"{path} لا يسجّل عدد المُنقَذ"
    assert "salvage_error" in src, f"{path} لا يسجّل سبب فشل الإنقاذ"


def test_runner_salvage_runs_before_saving():
    """الإنقاذ يجب أن يسبق الحفظ، وإلا حُفظت الأقسام قبل نقل الصفوف."""
    src = _source("services/analysis_runner.py")
    assert src.index(_SALVAGE_CALL) < src.index("state.persist_results()"), (
        "الإنقاذ يجري بعد الحفظ — النتائج المحفوظة لن تتضمّنه"
    )


def test_runner_salvage_phase_has_arabic_label():
    """كل مرحلة يكتبها المشغّل لها اسم عربي، وإلا ظهر مفتاح خام للمالك."""
    from services.analysis_runner import PHASE_LABELS

    assert "salvage" in PHASE_LABELS
    assert PHASE_LABELS["salvage"].strip()
