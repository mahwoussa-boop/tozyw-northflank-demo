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
