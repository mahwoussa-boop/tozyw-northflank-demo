# -*- coding: utf-8 -*-
"""Timing instrumentation must be present without changing matching results."""
import pandas as pd

from engines.engine import run_full_analysis


def test_matching_subphases_are_reported():
    our_df = pd.DataFrame({"اسم المنتج": ["Perfume Timing Alpha 100ml"], "السعر": [200.0]})
    comp_dfs = {
        "Store": pd.DataFrame({"اسم المنتج": ["Perfume Timing Alpha 100ml"], "السعر": [190.0]}),
    }

    memory_events = []

    result, audit = run_full_analysis(
        our_df,
        comp_dfs,
        use_ai=False,
        memory_callback=lambda stage, **metadata: memory_events.append((stage, metadata)),
    )

    assert len(result) == 1
    timings = audit["matching_subphases_sec"]
    assert set(timings) == {
        "brand_vocabulary", "comp_index", "candidate_matching",
        "ai_drain", "ledger_finalize", "result_materialization",
    }
    assert all(value >= 0 for value in timings.values())
    stages = [stage for stage, _metadata in memory_events]
    assert stages == [
        "after_comp_index", "candidate_matching", "before_ai_drain",
        "after_ai_drain", "before_result_materialization", "after_result_materialization",
    ]
    assert memory_events[0][1]["competitors"] == 1
    assert memory_events[0][1]["competitor_rows"] == 1


def test_release_comp_dfs_preserves_matching_result_and_clears_input():
    """التحرير بعد CompIndex يحفظ المرشحين لكنه لا يبقي DataFrames الأصلية حية."""
    our_df = pd.DataFrame({"اسم المنتج": ["Perfume Timing Alpha 100ml"], "السعر": [200.0]})
    baseline_comp = {
        "Store": pd.DataFrame({"اسم المنتج": ["Perfume Timing Alpha 100ml"], "السعر": [190.0]}),
    }
    baseline_result, baseline_audit = run_full_analysis(our_df, baseline_comp, use_ai=False)

    releasable_comp = {
        name: frame.copy(deep=True) for name, frame in baseline_comp.items()
    }
    released_result, released_audit = run_full_analysis(
        our_df,
        releasable_comp,
        use_ai=False,
        release_comp_dfs=True,
    )

    assert releasable_comp == {}
    pd.testing.assert_frame_equal(baseline_result, released_result)
    assert baseline_audit["processed"] == released_audit["processed"]
