# -*- coding: utf-8 -*-
"""قفل الكشط: يمنع كشطين متوازيين على القاعدة الحيّة (خطوة scrape-lock).

يُعزل مسار القفل بـmonkeypatch على get_data_dir كي لا يُلمَس data/ الحقيقي.
"""
import os
import time

import pytest

from core.exceptions import PricingError
from services import scraper_service
from services.scraper_service import ScraperService


def _boom(*_a, **_k):
    raise AssertionError("scrape_all ما كان يجب أن يعمل خلف قفل حيّ")


def test_scrape_all_rejects_when_lock_fresh(tmp_path, monkeypatch):
    """قفل حديث موجود ⇒ رفض أدبي (PricingError) دون تشغيل الكشط."""
    monkeypatch.setattr(scraper_service, "get_data_dir", lambda: str(tmp_path))
    (tmp_path / ".scrape.lock").write_text("999999,0")
    svc = ScraperService()
    monkeypatch.setattr(svc, "list_competitors", _boom)  # تِربواير: أي تشغيل ينفجر
    with pytest.raises(PricingError):
        svc.scrape_all()
    assert (tmp_path / ".scrape.lock").exists()  # لم نُحرِّر قفل غيرنا


def test_scrape_all_breaks_orphan_lock(tmp_path, monkeypatch):
    """قفل يتيم (عمر > 3 ساعات) ⇒ يُكسَر ويعمل الكشط ثم يُحرَّر في finally."""
    monkeypatch.setattr(scraper_service, "get_data_dir", lambda: str(tmp_path))
    lock = tmp_path / ".scrape.lock"
    lock.write_text("999999,0")
    old = time.time() - 4 * 3600
    os.utime(lock, (old, old))
    svc = ScraperService()
    monkeypatch.setattr(svc, "list_competitors", lambda: [])  # لا شبكة
    result = svc.scrape_all()
    assert result == {}
    assert not lock.exists()  # حُرِّر في finally
