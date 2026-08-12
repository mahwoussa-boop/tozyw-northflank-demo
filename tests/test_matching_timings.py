# -*- coding: utf-8 -*-
"""Timing instrumentation must be present without changing matching results."""
import pandas as pd

from engines.engine import run_full_analysis


def test_matching_subphases_are_reported():
    our_df = pd.DataFrame({"اسم المنتج": ["Perfume Timing Alpha 100ml"], "السعر": [200.0]})
    comp_dfs = {
        "Store": pd.DataFrame({"اسم المنتج": ["Perfume Timing Alpha 100ml"], "السعر": [190.0]}),
    }

    result, audit = run_full_analysis(our_df, comp_dfs, use_ai=False)

    assert len(result) == 1
    timings = audit["matching_subphases_sec"]
    assert set(timings) == {
        "brand_vocabulary", "comp_index", "candidate_matching",
        "ai_drain", "ledger_finalize", "result_materialization",
    }
    assert all(value >= 0 for value in timings.values())
