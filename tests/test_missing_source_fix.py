# -*- coding: utf-8 -*-
"""اختبار إصلاح تفتيت أسماء المتاجر عند المنبع (MissingService._build_row).

CompetitorIntelligence يسلّم ``competitors_list`` نصاً «حنان, ساره ستور»؛
قبل الإصلاح كان التكرار عليه حرفاً-حرفاً فيُنتج «ح، ن، ا، ن». بعد الإصلاح
يُقسَّم على "," وتبقى القوائم الحقيقية كما هي.
"""
from types import SimpleNamespace

from services.matching_service import Ownership
from services.missing_service import ClassifyKernel, MissingService


def _service() -> MissingService:
    ck = ClassifyKernel(
        classify_product=lambda n: "perfume",
        classify_category=lambda n: "🌸 عطور",
        extract_size=lambda n: 100.0,
        extract_brand=lambda n: "ماركة",
        is_sample=lambda n: False,
        is_tester=lambda n: False,
    )
    return MissingService(matching=object(), classify_kernel=ck)


def _outcome():
    return SimpleNamespace(
        ownership=Ownership.MISSING, score=50.0, our_match=None, reason="",
    )


def test_string_competitors_list_not_shredded():
    row = _service()._build_row(
        "عطر تجريبي 100مل", 99.0,
        {"competitors_list": "حنان, ساره ستور", "brand": "ديور",
         "competitor_count": 2},
        _outcome(),
    )
    assert row["المنافس"] == "حنان"
    assert row["المنافسون"] == "حنان، ساره ستور"


def test_real_list_passes_unchanged():
    row = _service()._build_row(
        "عطر تجريبي 100مل", 99.0,
        {"competitors_list": ["متجر أ", "متجر ب"], "brand": "ديور",
         "competitor_count": 2},
        _outcome(),
    )
    assert row["المنافس"] == "متجر أ"
    assert row["المنافسون"] == "متجر أ، متجر ب"


def test_empty_and_single_store():
    svc = _service()
    row = svc._build_row("عطر تجريبي", 50.0,
                         {"competitors_list": "", "competitor_count": 3},
                         _outcome())
    assert row["المنافس"] == "3 متجر" and row["المنافسون"] == ""
    row = svc._build_row("عطر تجريبي", 50.0,
                         {"competitors_list": "سولارا", "competitor_count": 1},
                         _outcome())
    assert row["المنافس"] == "سولارا" and row["المنافسون"] == "سولارا"
