"""tests/test_snapshot_persist.py — حارس أن لقطة الجلسة تُسلسَل إلى JSON وتعود سليمة.

عطل كامن: ``_serialize_snapshot`` كان يسلسل نماذج pydantic عبر ``model_dump()``
(نمط python) فيُبقي ``AnalysisResult.created_at`` (datetime) ومفاتيح
``section_counts`` (SectionType enum) ⇒ ``json.dump`` يرمي ``TypeError`` ⇒
``persist_results`` يبتلعه ويعيد False ⇒ لا ``ui_session.json`` ⇒ النتائج لا تظهر
ولا تنجو من إعادة التشغيل.

هذا الاختبار يبني ``AnalysisResult`` حقيقياً (مفاتيح enum + created_at datetime)،
يمرّره عبر ``_serialize_snapshot`` ثم ``json.dumps`` (يجب ألّا يرمي)، ثم يُعيد
تحميله عبر ``_deserialize_snapshot`` (round-trip) ويؤكّد أن ``AnalysisResult``
المُعاد صالح لعرض السلال (مفاتيح SectionType + مجاميع صحيحة). قبل الإصلاح يفشل؛ بعده يمرّ.
"""
import json

import pandas as pd

from core.enums import SectionType
from core.models import AnalysisResult, ReconciliationReport
from ui.state_manager import AppState, _deserialize_snapshot, _serialize_snapshot


def _make_result() -> AnalysisResult:
    counts = {
        SectionType.PRICE_RAISE: 3852, SectionType.PRICE_LOWER: 820,
        SectionType.APPROVED: 1446, SectionType.REVIEW: 547,
        SectionType.EXCLUDED: 4832, SectionType.MISSING: 20887,
    }
    rep = ReconciliationReport(
        all_count=11497, sum_buckets=11497,
        bucket_counts={"price_raise": 3852}, duplicate_count=0,
    )
    return AnalysisResult(
        section_counts=counts, total=11497, missing_count=20887,
        reconciliation=rep, duration_sec=2908.0,
    )


def _full_snapshot() -> dict:
    return {
        "our_catalog": None,
        "sections": {"price_raise": pd.DataFrame({"المنتج": ["أ", "ب", "ج"]})},
        "missing_df": None,
        "analysis_results": _make_result(),
        "processed_price_skus": set(),
        "processed_missing_urls": set(),
        "hidden_products": set(),
        "processed_log": [],
    }


def test_snapshot_serializes_to_json_without_raising():
    ser = _serialize_snapshot(_full_snapshot())
    # قبل الإصلاح: TypeError: Object of type datetime is not JSON serializable
    dumped = json.dumps(ser, ensure_ascii=False)
    assert isinstance(dumped, str) and len(dumped) > 0


def test_snapshot_roundtrip_restores_analysis_result_for_buckets():
    ser = _serialize_snapshot(_full_snapshot())
    back = _deserialize_snapshot(json.loads(json.dumps(ser, ensure_ascii=False)))
    ar = back["analysis_results"]
    assert isinstance(ar, AnalysisResult)
    # مفاتيح SectionType عادت صالحة لعرض السلال + المجاميع صحيحة
    assert ar.section_counts.get(SectionType.PRICE_RAISE) == 3852
    assert ar.section_counts.get(SectionType.PRICE_LOWER) == 820
    assert ar.total == 11497
    assert ar.reconciliation is not None and ar.reconciliation.gap == 0
    # DataFrame السلال نجا من الرحلة
    assert len(back["sections"]["price_raise"]) == 3


def test_reconcile_heals_frozen_section_counts():
    """لقطة قديمة: عدّاد مجمّد (excluded=3186) لكن الأقسام الحيّة نُقلت (2966)."""
    state = AppState()
    state.sections = {
        "excluded": pd.DataFrame({"المنتج": ["x"] * 2966}),
        "review": pd.DataFrame({"المنتج": ["y"] * 1204}),
        "approved": pd.DataFrame({"المنتج": ["z"] * 1735}),
    }
    # عدّاد مجمّد على أرقام ما قبل الإنقاذ + missing يجب أن يبقى
    state.analysis_results = AnalysisResult(
        section_counts={
            SectionType.EXCLUDED: 3186, SectionType.REVIEW: 1006,
            SectionType.APPROVED: 1734, SectionType.MISSING: 28545,
        },
        total=11511, missing_count=28545,
    )
    state._reconcile_section_counts()
    sc = state.analysis_results.section_counts
    assert sc[SectionType.EXCLUDED] == 2966   # صُحّح للأقسام الحيّة
    assert sc[SectionType.REVIEW] == 1204
    assert sc[SectionType.APPROVED] == 1735
    assert sc[SectionType.MISSING] == 28545   # لم يُمَس (ليس ضمن sections)


def test_reconcile_noop_when_no_results():
    state = AppState()
    state.sections = {"excluded": pd.DataFrame({"المنتج": ["x"]})}
    state.analysis_results = None
    state._reconcile_section_counts()  # لا يرمي
    assert state.analysis_results is None
