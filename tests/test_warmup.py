# -*- coding: utf-8 -*-
"""اختبارات التسخين المسبق (services/warmup.py) والمذكّرة على مستوى العملية.

الغرض المحمي: أول فتح لـ«جديد عند المنافسين» بعد إعادة تشغيل الخادم كان يدفع
5,941م.ث مقيسة لبناء المُطابِق. التسخين ينقل الكلفة لخيط الإقلاع — بشرطين
لا يُسمح بكسرهما: (1) فشل التسخين لا يكسر شيئاً، (2) لا بناء مزدوج.
"""
from __future__ import annotations

import sys
import threading

import pytest

import services.ownership_service as osvc
import services.warmup as warmup


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """يعزل المذكّرة وحارس الإطلاق بين الاختبارات."""
    monkeypatch.setattr(osvc, "_MATCHER_MEMO", {})
    monkeypatch.setattr(warmup, "_started", False)
    yield


# ── المذكّرة على مستوى العملية ────────────────────────────────────────────────
def test_memo_returns_same_object_for_same_signature(monkeypatch):
    calls = []

    def _fake():
        calls.append(1)
        return ("MATCHER", {"a": "1"})

    monkeypatch.setattr(osvc, "build_ownership_matcher", _fake)
    first = osvc.build_ownership_matcher_cached("sig-1")
    second = osvc.build_ownership_matcher_cached("sig-1")

    assert first is second
    assert len(calls) == 1, "بصمة واحدة يجب ألا تُعيد البناء"


def test_memo_rebuilds_when_signature_changes(monkeypatch):
    monkeypatch.setattr(osvc, "build_ownership_matcher",
                        lambda: (object(), {}))
    first = osvc.build_ownership_matcher_cached("sig-1")
    second = osvc.build_ownership_matcher_cached("sig-2")

    assert first is not second, "كتالوج تغيّر ⇒ مُطابِق جديد"


def test_memo_is_capped_so_old_matchers_do_not_accumulate(monkeypatch):
    monkeypatch.setattr(osvc, "build_ownership_matcher",
                        lambda: (object(), {}))
    for i in range(5):
        osvc.build_ownership_matcher_cached(f"sig-{i}")

    assert len(osvc._MATCHER_MEMO) <= osvc._MATCHER_MEMO_MAX
    assert "sig-4" in osvc._MATCHER_MEMO, "الأحدث يبقى"


def test_concurrent_builds_run_exactly_once(monkeypatch):
    """خيط التسخين وأول جلسة متصفّح قد يتسابقان — بناءٌ واحد لا اثنان.

    لو بنى كلٌّ نسخته لدفع جهاز 7.8غ.ب ضعف الحساب وضعف الذاكرة لحظياً.
    """
    ready = threading.Event()
    builds: list = []

    def _slow():
        builds.append(1)
        ready.wait(timeout=5)
        return (object(), {})

    monkeypatch.setattr(osvc, "build_ownership_matcher", _slow)
    results: list = []
    threads = [
        threading.Thread(target=lambda: results.append(
            osvc.build_ownership_matcher_cached("same")))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    ready.set()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 3
    assert results[0] is results[1] is results[2], "لا مُطابِقات متعدّدة لنفس البصمة"
    assert len(builds) == 1, "بناء واحد فقط رغم ثلاثة طالبين متزامنين"


# ── إطلاق التسخين ────────────────────────────────────────────────────────────
def test_warmup_is_disabled_under_pytest():
    """الحارس الذي يمنع خيطاً في كل اختبار AppTest — لو سقط لصار الاختبار بطيئاً وضاجّاً."""
    assert "pytest" in sys.modules
    assert warmup.start_background_warmup() is False


def test_warmup_respects_kill_switch(monkeypatch):
    monkeypatch.setenv("MAHWOUS_WARMUP", "0")
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    try:
        assert warmup.start_background_warmup() is False
    finally:
        sys.modules["pytest"] = pytest


def test_warmup_starts_once_per_process(monkeypatch):
    started: list = []
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    class _FakeTimer:
        def __init__(self, delay, fn):
            self.daemon = False
            started.append(fn)

        def start(self):
            pass

    monkeypatch.setattr(warmup.threading, "Timer", _FakeTimer)
    try:
        assert warmup.start_background_warmup() is True
        assert warmup.start_background_warmup() is False, "لا خيط لكل إعادة رسم"
    finally:
        sys.modules["pytest"] = pytest
    assert len(started) == 1


def test_warm_swallows_failures(monkeypatch):
    """فشل التسخين يعني «بطء كما كان» لا خادماً مكسوراً."""
    def _boom(_sig):
        raise RuntimeError("قاعدة مقفلة")

    monkeypatch.setattr(osvc, "build_ownership_matcher_cached", _boom)
    warmup._warm()          # يجب ألا يرمي
