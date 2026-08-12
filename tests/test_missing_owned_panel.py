# -*- coding: utf-8 -*-
"""معاينة داخل العملية للوحة «قد تملكه باسم آخر» في صفحة المفقودات.

تثبت: (1) الصفحة تُقلع بلا استثناء مع اللوحة، (2) الفحص يجد المطابقة الحقيقية
ويتجاهل المفقود الفعلي، (3) «أملكها كلها» يُخفي المُطابَق فقط (إخفاء قابل للتراجع)."""
import pandas as pd
from streamlit.testing.v1 import AppTest

from ui.state_manager import AppState
from conf.constants import COL_COMP_NAME, COL_COMP_LINK

_OWNED = "عطر مايكل كورس النسائي او دو بارفيوم 100مل"
_GENUINE = "عطر هوغو بوس اكس اكس النسائي تواليت 100مل"


def _boot():
    s = AppState()
    s.sections = {"price_raise": pd.DataFrame(
        {"المنتج": ["منتج"], "السعر": [100.0], "سعر_المنافس": [80.0]})}
    s.missing_df = pd.DataFrame({
        COL_COMP_NAME: [_OWNED, _GENUINE],
        "مستوى_الثقة": ["green", "green"],
        COL_COMP_LINK: ["http://x/1", "http://x/2"],
        "سعر_المنافس": [100.0, 120.0],
    })
    s.our_catalog = pd.DataFrame(
        {"المنتج": ["عطر مايكل كورس النسائي أو دو بارفيوم 100 مل"]})
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔍 منتجات مفقودة"
    at.run()
    return at


def test_owned_panel_scan_and_hide():
    at = _boot()
    assert not at.exception, list(at.exception)

    # الفحص الدقيق
    at.button(key="owned_scan").click().run()
    assert not at.exception, list(at.exception)

    # المطابقة الحقيقية وُجدت ⇒ زر «أملكها كلها» ظاهر؛ نضغطه
    at.button(key="owned_hide_all").click().run()
    assert not at.exception, list(at.exception)

    s = at.session_state["_app_state_v2"]
    assert s.is_hidden(_OWNED), "المُطابَق يجب أن يُخفى"
    assert not s.is_hidden(_GENUINE), "المفقود الفعلي يجب أن يبقى"


# منتج نملكه لكن التركيز غير مذكور عند المنافس ⇒ مطابقة «محتمل» (لا صارمة)
_POSSIBLE = "عطر بولغاري مان وود ايسنس 60 مل"
_POSSIBLE_OURS = "عطر بولغاري مان وود إيسنس أو دو برفيوم 60 مل"


def _boot_possible():
    s = AppState()
    s.sections = {"price_raise": pd.DataFrame(
        {"المنتج": ["منتج"], "السعر": [100.0], "سعر_المنافس": [80.0]})}
    s.missing_df = pd.DataFrame({
        COL_COMP_NAME: [_POSSIBLE, _GENUINE],
        "مستوى_الثقة": ["green", "green"],
        COL_COMP_LINK: ["http://x/3", "http://x/2"],
        "سعر_المنافس": [200.0, 120.0],
    })
    s.our_catalog = pd.DataFrame({"المنتج": [_POSSIBLE_OURS]})
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔍 منتجات مفقودة"
    at.run()
    return at


def test_possible_panel_scan_and_hide():
    """الطبقة «محتمل»: تظهر تحت الصارمة، وزرّ «أملكه» يُخفي المُطابَق المحتمل فقط."""
    at = _boot_possible()
    assert not at.exception, list(at.exception)

    at.button(key="owned_scan").click().run()
    assert not at.exception, list(at.exception)

    # زرّ الطبقة المحتملة موجود (poss_hide_0) بينما الجماعي (owned_hide_all) غائب
    poss = [b for b in at.button if b.key == "poss_hide_0"]
    assert poss, "زرّ المطابقة المحتملة يجب أن يظهر"
    assert not [b for b in at.button if b.key == "owned_hide_all"], \
        "لا زرّ جماعي حين لا مطابقات صارمة"

    poss[0].click().run()
    assert not at.exception, list(at.exception)
    s = at.session_state["_app_state_v2"]
    assert s.is_hidden(_POSSIBLE), "المطابق المحتمل يُخفى بعد «أملكه»"
    assert not s.is_hidden(_GENUINE), "المفقود الفعلي يبقى"


# ── فرز «الأحدث رصداً» + شارة 🆕 داخل العملية (الطلب 2) ────────────────
def test_newest_sort_and_new_badge_render():
    """الصفحة تُقلع مع خيار «الأحدث رصداً» وتعرض شارة 🆕 لمنتج رُصد اليوم."""
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")

    s = AppState()
    s.sections = {"price_raise": pd.DataFrame(
        {"المنتج": ["منتج"], "السعر": [100.0], "سعر_المنافس": [80.0]})}
    s.missing_df = pd.DataFrame({
        COL_COMP_NAME: ["عطر قديم لا نملكه 100مل", "عطر جديد اليوم لا نملكه 100مل"],
        "مستوى_الثقة": ["green", "green"],
        COL_COMP_LINK: ["http://x/10", "http://x/11"],
        "سعر_المنافس": [100.0, 120.0],
        "تاريخ_أول_رصد": [old, today],
    })
    s.our_catalog = pd.DataFrame({"المنتج": ["منتج مختلف تماماً"]})
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔍 منتجات مفقودة"
    at.session_state["missing_conf"] = "مؤكد"
    at.session_state["missing_sort"] = "الأحدث رصداً"
    at.run()
    assert not at.exception, list(at.exception)

    # شارة 🆕 تظهر لمنتج اليوم (في أي caption على الصفحة)
    captions = " ".join(c.value for c in at.caption)
    assert "🆕" in captions, "شارة 🆕 يجب أن تظهر للمنتج المرصود حديثاً"


# ── وسم الملف التقييمي للمنتج «⭐» داخل العملية (الطلب 3 م1) ───────────
def test_top_competitor_badge_render(tmp_path, monkeypatch):
    """الصفحة تعرض الملف التقييمي السوقي للمنتج + أقوى بائع تحت البطاقة."""
    from engines.mahally_scraper import MahallyScraper
    import ui.pages.missing as mp

    comp_name = "عطر مفقود له منافس قوي 100مل"
    key = MahallyScraper.normalize(comp_name)
    # نحقن كاش الملف التقييمي مباشرة (نتفادى القاعدة الحيّة تماماً)
    monkeypatch.setattr(mp, "_topcomp_loaded", True, raising=False)
    monkeypatch.setattr(
        mp, "_topcomp_cache",
        {key: {"competitor": "متجر_قوي", "rating_count": 321, "rating_avg": 4.6,
               "total_rating_count": 500, "market_rating_avg": 4.5, "store_count": 3}},
        raising=False,
    )

    s = AppState()
    s.sections = {"price_raise": pd.DataFrame(
        {"المنتج": ["منتج"], "السعر": [100.0], "سعر_المنافس": [80.0]})}
    s.missing_df = pd.DataFrame({
        COL_COMP_NAME: [comp_name],
        "مستوى_الثقة": ["green"],
        COL_COMP_LINK: ["http://x/50"],
        "سعر_المنافس": [150.0],
    })
    s.our_catalog = pd.DataFrame({"المنتج": ["منتج مختلف تماماً"]})
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔍 منتجات مفقودة"
    at.session_state["missing_conf"] = "مؤكد"
    at.run()
    assert not at.exception, list(at.exception)

    captions = " ".join(c.value for c in at.caption)
    assert "500 تقييم بالسوق" in captions       # إشارة المنتج السوقية أولاً
    assert "الأقوى: متجر_قوي (321)" in captions  # ثم أقوى بائع
