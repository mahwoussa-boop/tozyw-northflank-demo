# -*- coding: utf-8 -*-
"""اختبار تخسيس اللقطة: «الوصف» يُسقَط من النسخة المُسلسَلة فقط.

الكتالوج الحي في الذاكرة يحتفظ بالعمود؛ اللقطة على القرص بدونه؛
ودورة حفظ/استعادة كاملة تمرّ بباقي الأعمدة سليمة.

حُدِّث 2026-07-25 لصيغة اللقطة 3 (مجلّد ``ui_session/`` فيه ملف لكل إطار بدل
ملف واحد 72.8 م.ب). الحمولة داخل كل ملف هي **نفسها** ناتج
``to_json(orient="split", force_ascii=False)`` — فمنطق التخسيس والترميز لم يتغيّر.
"""
import json

import pandas as pd
import pytest

import ui.state_manager as sm
from ui.state_manager import AppState


@pytest.fixture()
def snapshot_dir(tmp_path, monkeypatch):
    """يعزل اللقطة: ترقيع ``_SNAPSHOT_PATH`` وحده يكفي — المجلّد مشتقّ منه."""
    monkeypatch.setattr(sm, "_SNAPSHOT_PATH", str(tmp_path / "ui_session.json"))
    return tmp_path / "ui_session"


def _state() -> AppState:
    state = AppState()
    state.our_catalog = pd.DataFrame({
        "المنتج": ["عطر أ", "عطر ب"],
        "الماركة": ["ديور", "شانيل"],
        "سعر المنتج": [100.0, 200.0],
        "الوصف": ["<p>وصف طويل جداً</p>", "<p>وصف آخر</p>"],
        "وصف صفحة المنتج": ["<p>سيو طويل</p>", "<p>سيو آخر</p>"],
    })
    state.sections = {"price_raise": pd.DataFrame({"المنتج": ["عطر أ"]})}
    state.missing_df = pd.DataFrame({"منتج_المنافس": ["عطر ج"]})
    return state


def test_saved_snapshot_has_no_description(snapshot_dir):
    state = _state()
    assert state.persist_results() is True
    our_cat_data = json.loads((snapshot_dir / "our_catalog.json").read_text(encoding="utf-8"))
    assert "الوصف" not in our_cat_data["columns"]
    assert "وصف صفحة المنتج" not in our_cat_data["columns"]
    assert our_cat_data["columns"] == ["المنتج", "الماركة", "سعر المنتج"]
    assert our_cat_data["data"][0][1] == "ديور"
    assert our_cat_data["data"][1][1] == "شانيل"


def test_snapshot_not_ascii_escaped(snapshot_dir):
    """force_ascii=False: العربية تُكتب حروفاً لا ‎\\uXXXX (تضخيم ~3× سابقاً)."""
    state = _state()
    assert state.persist_results() is True
    raw = (snapshot_dir / "our_catalog.json").read_text(encoding="utf-8")
    assert "ديور" in raw          # حروف عربية خام داخل حمولة DataFrame
    assert "\\u062f\\u064a" not in raw  # لا تهريب ديور القديم


def test_live_catalog_untouched(snapshot_dir):
    state = _state()
    state.persist_results()
    assert "الوصف" in state.our_catalog.columns  # الحي لم يُمَس
    assert "وصف صفحة المنتج" in state.our_catalog.columns
    assert len(state.our_catalog) == 2


def test_full_save_restore_cycle(snapshot_dir):
    _state().persist_results()
    fresh = AppState()
    assert fresh.restore_results() is True
    assert "الوصف" not in fresh.our_catalog.columns
    assert fresh.our_catalog["المنتج"].tolist() == ["عطر أ", "عطر ب"]
    assert len(fresh.missing_df) == 1
    # حفظ ثانٍ من حالة مستعادة (بلا «الوصف» أصلاً) يمرّ دون خطأ
    assert fresh.persist_results() is True


def test_catalog_none_is_safe(snapshot_dir):
    state = _state()
    state.our_catalog = None
    assert state.persist_results() is True
    meta = json.loads((snapshot_dir / "_meta.json").read_text(encoding="utf-8"))
    assert "our_catalog" not in meta["frames"]     # لا ملف كتالوج أصلاً
    assert not (snapshot_dir / "our_catalog.json").exists()
    fresh = AppState()
    assert fresh.restore_results() is True
    assert fresh.our_catalog is None
