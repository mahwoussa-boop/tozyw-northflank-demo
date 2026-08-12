# -*- coding: utf-8 -*-
"""اختبار زرع قواعد إعادة التصنيف (R1-R3) في مصنع الصفوف MissingService._build_row.

الترقية تحدث داخل المصنع نفسه: صف «متوفّر بحجم مختلف» بدرجة ≥82 يخرج green،
وR3 تعمل فقط عند تمرير فهرس اسم←ماركة (غيابه = تعطيل آمن).
"""
from types import SimpleNamespace

from services.matching_service import Ownership
from services.missing_service import ClassifyKernel, MissingService


def _service(our_brands=None) -> MissingService:
    ck = ClassifyKernel(
        classify_product=lambda n: "perfume",
        classify_category=lambda n: "🌸 عطور",
        extract_size=lambda n: 100.0,
        extract_brand=lambda n: "",
        is_sample=lambda n: False,
        is_tester=lambda n: False,
    )
    return MissingService(matching=object(), classify_kernel=ck,
                          our_brands=our_brands)


def _review_outcome(score: float, reason: str, our_match: str = "منتجنا س"):
    return SimpleNamespace(ownership=Ownership.REVIEW, score=score,
                           our_match=our_match, reason=reason)


CAND = {"competitors_list": "حنان", "brand": "شانيل", "competitor_count": 1}


def test_r1_size_diff_leaves_factory_green():
    row = _service()._build_row(
        "عطر تجريبي 50مل", 99.0, CAND,
        _review_outcome(90.0, "متوفّر بحجم مختلف"),
    )
    assert row["مستوى_الثقة"] == "green"
    assert row["حالة_المراجعة"] == ""
    assert "نسخة حجم" in row["سبب_النقل"] and "90%" in row["سبب_النقل"]


def test_r3_cross_brand_with_index():
    row = _service({"منتجنا س": "ديور"})._build_row(
        "عطر تجريبي 100مل", 99.0, CAND,
        _review_outcome(70.0, "بانتظار التحقق"),
    )
    assert row["مستوى_الثقة"] == "green"
    assert row["سبب_النقل"] == "تشابه اسمي عابر للماركات"


def test_r3_disabled_without_index_stays_review():
    row = _service(our_brands=None)._build_row(
        "عطر تجريبي 100مل", 99.0, CAND,
        _review_outcome(70.0, "بانتظار التحقق"),
    )
    assert row["مستوى_الثقة"] == "review"
    assert row["حالة_المراجعة"] == "بانتظار التحقق"
    assert "سبب_النقل" not in row


def test_green_passthrough_untouched():
    row = _service({"منتجنا س": "ديور"})._build_row(
        "عطر تجريبي 100مل", 99.0, CAND,
        SimpleNamespace(ownership=Ownership.MISSING, score=40.0,
                        our_match=None, reason=""),
    )
    assert row["مستوى_الثقة"] == "green"
    assert row["حالة_المراجعة"] == ""
    assert "سبب_النقل" not in row  # green أصلية — لا تُوسم بنقل


def test_pending_low_score_stays_review():
    row = _service({"منتجنا س": "ديور"})._build_row(
        "عطر تجريبي 100مل", 99.0, CAND,
        _review_outcome(60.0, "بانتظار التحقق"),
    )
    assert row["مستوى_الثقة"] == "review"
