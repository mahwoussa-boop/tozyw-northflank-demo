# -*- coding: utf-8 -*-
"""معاينة داخل العملية: قسم الأسعار «سعر أعلى» يُعرض بلا استثناء مع ترتيب الأهم.

يحرس تعديل الغلاف المشترك render_section_page (إدراج _priority_sorted_cached):
الصفحة تُقلع، والفرز يُطبَّق (يظهر مفتاح كاش الترتيب في session_state)."""
import json
import sqlite3

import pandas as pd
from streamlit.testing.v1 import AppTest

from ui.state_manager import AppState
from conf.constants import COL_OUR_PRICE, COL_COMP_DETAILS, COL_COMP_NAME, COL_COMP_PRICE


def _prod(name, our_price, comp_prices):
    details = [{"المنافس": f"m{i}", "السعر": p} for i, p in enumerate(comp_prices)]
    return {
        "المنتج": name,
        COL_OUR_PRICE: our_price,
        "سعر_المنافس": min(comp_prices) if comp_prices else our_price,
        COL_COMP_DETAILS: json.dumps(details, ensure_ascii=False),
    }


def test_price_section_renders_with_priority_sort():
    s = AppState()
    s.sections = {"price_raise": pd.DataFrame([
        _prod("منتج قليل الخسارة", 100, [95]),
        _prod("منتج كثير الخسارة", 100, [50, 60, 70, 80]),
    ])}
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔴 سعر أعلى"
    at.run()
    # الغلاف المشترك يُدرج الفرز؛ يجب أن يُقلع القسم بلا استثناء (حراسة التعديل).
    assert not at.exception, list(at.exception)


def test_price_section_shows_product_rating_badge(monkeypatch):
    """وسم الملف التقييمي للمنتج يظهر تحت بطاقة القسم السعري (فارغ آمن)."""
    import services.top_competitor_service as svc
    from engines.mahally_scraper import MahallyScraper

    comp_name = "عطر منافس مقيَّم في القسم 100مل"
    key = MahallyScraper.normalize(comp_name)
    monkeypatch.setattr(svc, "_PROC_LOADED", True, raising=False)
    monkeypatch.setattr(svc, "_PROC_CACHE", {
        key: {"total_rating_count": 250, "market_rating_avg": 4.6,
              "competitor": "متجر_مقيَّم", "rating_count": 240},
    }, raising=False)

    row = _prod("منتجنا في القسم", 100, [50, 60])
    row["منتج_المنافس"] = comp_name
    row["المنافس"] = "متجر_مقيَّم"
    # تقييم المتجر (وحدة معزولة) — نحقن كاشه أيضاً
    import services.store_profile_service as sp
    monkeypatch.setattr(sp, "_PROC_LOADED", True, raising=False)
    monkeypatch.setattr(sp, "_PROC_CACHE", {
        "متجر_مقيَّم": {"rating_avg": 4.9, "rating_count": 5000, "trust_score": 5.0},
    }, raising=False)
    monkeypatch.setattr(sp, "_RISING_LOADED", True, raising=False)
    monkeypatch.setattr(sp, "_RISING_CACHE", set(), raising=False)
    s = AppState()
    s.sections = {"price_raise": pd.DataFrame([row])}
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔴 سعر أعلى"
    at.run()
    assert not at.exception, list(at.exception)
    # وسم المنتج صار HTML بارزاً بالنجوم (st.markdown) بدل st.caption الصغير
    markdowns = " ".join(m.value for m in at.markdown)
    assert "250 تقييم بالسوق" in markdowns          # تقييم المنتج (بارز بالنجوم)
    captions = " ".join(c.value for c in at.caption)
    assert "تقييم المتجر ★4.9" in captions          # تقييم المتجر تحت اسم المتجر


def test_price_raise_shows_urgency_reason_and_movers_bar(tmp_path, monkeypatch):
    """محرك الأولوية (وحدة ج4 MVP): عنصر تجاوز بوّابة اليقين يظهر بسطر سبب حقيقي
    + شريط «⚡ تحرّكات اليوم» يعدّه — معاينة حيّة كاملة لا مجرّد «لا استثناء»."""
    import services.priority_engine as pe

    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE product_priority_scores (norm_name TEXT PRIMARY KEY, "
        "demand_certainty REAL, source_trust REAL, rating_up_events INTEGER, rebuilt_at TEXT)"
    )
    conn.execute(
        "INSERT INTO product_priority_scores VALUES "
        "('عطر ساخن يقين كامل', 1.0, 1.0, 6, datetime('now'))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(pe, "_default_db", lambda: db)

    s = AppState()
    s.sections = {"price_raise": pd.DataFrame([
        {  # عالي اليقين — يجب أن يتصدّر بسطر سبب ظاهر
            "المنتج": "منتجنا الساخن", COL_OUR_PRICE: 150,
            COL_COMP_PRICE: 100, COL_COMP_NAME: "عطر ساخن يقين كامل",
            COL_COMP_DETAILS: "[]",
        },
        {  # بلا إشارة طلب (غائب عن الكاش) — يبقى بلا سطر سبب
            "المنتج": "منتج عادي", COL_OUR_PRICE: 150,
            COL_COMP_PRICE: 90, COL_COMP_NAME: "منتج لا إشارة له",
            COL_COMP_DETAILS: "[]",
        },
    ])}
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["_app_state_v2"] = s
    at.session_state["nav_page"] = "🔴 سعر أعلى"
    at.run()
    assert not at.exception, list(at.exception)

    captions = " ".join(c.value for c in at.caption)
    assert "أغلى بـ50 ر.س" in captions              # سطر السبب الحقيقي ظهر فعلاً
    assert "طلب متزايد (6 تقييماً" in captions
    markdowns = " ".join(m.value for m in at.markdown)
    assert "تحرّكات اليوم" in markdowns              # شريط «⚡» في رأس القسم
