"""tests/test_notification_dedup.py — حارس منع ازدواج إشعارات الفئة.

يثبّت أن ``mark_category_read`` يطوي إشعارات الفئة السابقة (read=1، بلا حذف)
فيظهر أحدث رقم فقط لكل فئة — وهو إصلاح ازدواج «تحت المراجعة» (545 ثم 547).
"""
import os
import tempfile

import pytest

from infrastructure.db_manager import DatabaseManager
from services.workflow.notification_engine import NotificationEngine


@pytest.fixture
def engine():
    tmp = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(tmp)
    eng = NotificationEngine(db)
    eng.ensure_table()
    yield eng
    try:
        os.unlink(tmp)
    except OSError:
        pass


def test_mark_category_read_keeps_only_newest(engine):
    """إشعاران متتاليان لنفس الفئة + طيّ بينهما ⇒ الأحدث فقط برقمه."""
    engine.notify("⚠️ منتجات تحت المراجعة", "545 منتج بحاجة لقرار يدوي",
                  category="analysis")
    folded = engine.mark_category_read("analysis")
    engine.notify("⚠️ منتجات تحت المراجعة", "547 منتج بحاجة لقرار يدوي",
                  category="analysis")

    unread = engine.get_unread()
    assert folded == 1
    assert len(unread) == 1
    assert "547" in unread[0].body


def test_old_rows_survive_as_history(engine):
    """الطيّ لا يحذف: الصفّ القديم يبقى (read=1) في get_all كتاريخ."""
    engine.notify("قديم", "545", category="analysis")
    engine.mark_category_read("analysis")
    engine.notify("جديد", "547", category="analysis")

    assert len(engine.get_all()) == 2          # لا حذف — كلاهما محفوظ
    assert engine.unread_count() == 1           # غير المقروء واحد فقط


def test_other_category_unaffected(engine):
    """طيّ فئة لا يمسّ فئة أخرى."""
    engine.notify("سعر منافس", "تغيّر سعر", category="price_change")
    engine.notify("تحليل قديم", "545", category="analysis")

    engine.mark_category_read("analysis")

    unread = engine.get_unread()
    cats = {n.category for n in unread}
    assert cats == {"price_change"}
    assert len(unread) == 1


def test_mark_empty_category_returns_zero(engine):
    """فئة بلا إشعارات غير مقروءة ⇒ صفر (لا خطأ)."""
    assert engine.mark_category_read("analysis") == 0
