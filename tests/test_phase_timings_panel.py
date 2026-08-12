# -*- coding: utf-8 -*-
"""لوحة «أين ذهب زمن التحليل؟» — النواة الخالصة + معاينة الصفحة.

الغرض المحمي: قياس أطوار التحليل يُكتب في ملف التقدّم، وهذا ما يجعله
**مقروءاً** بدل أن يصير حقلاً كتابياً ميتاً (سابقة ``audit_stats``).
"""
from __future__ import annotations

from ui.pages.dashboard import phase_timing_rows


def test_rows_sorted_by_time_descending():
    rows = phase_timing_rows({"phase_timings": {
        "boot": 10.0, "matching": 300.0, "saving": 50.0,
    }})
    assert [r[1] for r in rows] == [300.0, 50.0, 10.0]


def test_percentages_sum_to_100():
    rows = phase_timing_rows({"phase_timings": {"a": 25.0, "b": 75.0}})
    assert abs(sum(r[2] for r in rows) - 100.0) < 0.01


def test_percentage_uses_phase_sum_not_total_duration():
    """لو اختلف مجموع الأطوار عن الزمن الكلي (انقطاع) فالنسب تبقى متّسقة."""
    rows = phase_timing_rows({
        "phase_timings": {"a": 10.0, "b": 10.0},
        "duration_sec": 1000.0,          # لا يُستعمل عمداً
    })
    assert all(abs(r[2] - 50.0) < 0.01 for r in rows)


def test_known_phases_get_arabic_labels():
    rows = phase_timing_rows({"phase_timings": {"matching": 5.0, "boot": 1.0}})
    labels = [r[0] for r in rows]
    assert "مطابقة المنافسين والتحليل السعري (الأطول)" in labels
    assert any("تهيئة" in lb for lb in labels)      # boot ليس في PHASE_LABELS


def test_empty_or_broken_input_yields_nothing_not_an_error():
    """حالة فارغة واضحة — لا تكسر لوحة التحكم (قاعدة 7)."""
    assert phase_timing_rows({}) == []
    assert phase_timing_rows({"phase_timings": {}}) == []
    assert phase_timing_rows({"phase_timings": {"a": "نص"}}) == []
    assert phase_timing_rows({"phase_timings": {"a": 0.0}}) == []
    assert phase_timing_rows({"phase_timings": "ليس قاموساً"}) == []


def test_negative_values_are_ignored():
    rows = phase_timing_rows({"phase_timings": {"a": -5.0, "b": 10.0}})
    assert len(rows) == 1 and rows[0][1] == 10.0
