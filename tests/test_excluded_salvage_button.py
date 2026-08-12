# -*- coding: utf-8 -*-
"""معاينة داخل العملية: زر «🔎 إنقاذ فوري» في صفحة المستبعد.

يحرس: الصفحة تُقلع بلا استثناء مع وجود مستبعدات · الزر الحتمي موجود · ضغطه
ينقل المطابق القوي من «مستبعد» إلى «مراجعة» (بكتالوج منافسين وهمي صغير — لا
اعتماد على القاعدة الحيّة).
"""
import pandas as pd
from streamlit.testing.v1 import AppTest

import ui.pages.excluded as excluded
from conf.constants import COL_BRAND, COL_OUR_ID, COL_OUR_NAME, COL_OUR_PRICE, COL_SIZE, COL_TYPE
from ui.state_manager import AppState

_APP_STATE_KEY = "_app_state_v2"


def _excluded_section(names):
    return pd.DataFrame([{
        COL_OUR_NAME: n, COL_OUR_PRICE: 150.0, COL_OUR_ID: str(i),
        COL_BRAND: "", COL_SIZE: "100", COL_TYPE: "EDP", "NO": str(i),
        "القرار": "⚪ مستبعد (لا يوجد تطابق)",
    } for i, n in enumerate(names)])


def _fake_pool():
    return pd.DataFrame([{
        "product_name": "عطر ليلة خميس للرجال او دي برفيوم 100 مل",
        "price": 120.0, "product_url": "http://x/1", "image_url": "",
        "competitor": "متجر س", "size": "100", "gender": "",
    }])


def _boot(monkeypatch, names):
    monkeypatch.setattr(excluded, "_load_pool_cached", _fake_pool)
    state = AppState()
    state.sections = {"excluded": _excluded_section(names)}
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state[_APP_STATE_KEY] = state
    at.session_state["nav_page"] = "⚪ مستبعد"
    return at.run()


def test_excluded_page_renders_with_salvage_button(monkeypatch):
    at = _boot(monkeypatch, ["عطر ليلة خميس للرجال 100مل"])
    assert not at.exception, list(at.exception)
    keys = [b.key for b in at.button]
    assert "salvage_excluded_det" in keys


def test_salvage_button_moves_match_to_review(monkeypatch):
    at = _boot(monkeypatch, ["عطر ليلة خميس للرجال 100مل"])
    btn = [b for b in at.button if b.key == "salvage_excluded_det"]
    assert btn, "زر الإنقاذ الحتمي غير موجود"
    at2 = btn[0].click().run()
    assert not at2.exception, list(at2.exception)

    state = at2.session_state[_APP_STATE_KEY]
    assert len(state.sections["excluded"]) == 0        # خرج المطابق من المستبعد
    assert len(state.sections.get("review", [])) == 1  # ودخل المراجعة
