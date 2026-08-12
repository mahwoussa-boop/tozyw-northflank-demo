# -*- coding: utf-8 -*-
"""معاينة داخل العملية: toggle «🃏 عرض كبطاقات» في رادار الفرص (الخطوة 3).

يحرس: الصفحة تُقلع بلا استثناء في الوضعين (جدول افتراضي · بطاقات عند التفعيل)،
والبطاقات تُبنى عبر الجسر opportunity_to_card_row + render_missing_card. نحقن
top_opportunities وهمياً فلا نعتمد على القاعدة الحيّة. + وحدة نص الشارة (نقيّة).
"""
from streamlit.testing.v1 import AppTest

import ui.pages.opportunity_radar as radar
from services.opportunity_service import Opportunity
from ui.state_manager import AppState


def _opp(name, score, out, sugg):
    return Opportunity(
        norm_name=name, store_count=6, out_ratio=out,
        price_min=90.0, price_median=110.0, price_max=130.0,
        instock_price_min=100.0, rating_avg=4.0, rating_count=5,
        in_our_catalog=False, suggested_price=sugg, score=score,
        data_age_days=1.0, image_url=None, product_url=None,
    )


def _fake_opps(**_kw):
    return [_opp("عطر فرصة أ", 0.8, 0.6, 99.0), _opp("عطر فرصة ب", 0.4, 0.2, None)]


def _boot(monkeypatch, *, cards):
    monkeypatch.setattr(radar, "top_opportunities", _fake_opps)
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["_app_state_v2"] = AppState()
    at.session_state["nav_page"] = "🎯 رادار الفرص"
    if cards:
        at.session_state["opp_cards"] = True
    return at.run()


def test_radar_table_default_renders(monkeypatch):
    at = _boot(monkeypatch, cards=False)          # الجدول الافتراضي (toggle off)
    assert not at.exception, list(at.exception)


def test_radar_cards_toggle_renders_cards(monkeypatch):
    at = _boot(monkeypatch, cards=True)
    assert not at.exception, list(at.exception)
    blob = " ".join(m.value for m in at.markdown)
    assert "mhw-card" in blob                      # البطاقة رُسمت عبر المكوّن


def test_radar_badge_text_stale_safe():
    txt = radar._radar_badge_text(
        {"score": 0.8, "out_ratio": 0.6, "السعر_المقترح": None})
    assert "درجة 80%" in txt and "نفاد 60%" in txt and "مقترح —" in txt
