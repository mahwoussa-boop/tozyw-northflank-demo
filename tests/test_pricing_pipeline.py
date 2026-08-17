"""tests/test_pricing_pipeline.py — اختبارات الأنبوب السعري (P6).

يغطّي:
  1) ``load_competitor_dfs`` ضدّ قاعدة SQLite صغيرة مؤقتة (تسمية + تجميع).
  2) ``run_pricing_analysis`` بكاش مزروع (لا تشغيل للمحرّك الثقيل) — يتحقّق من
     التصنيف الصحيح، التوازن (gap=0)، وعدّ الأقسام بما فيها المفقودات.
  3) حارس التكرار: مفقود يطابق قسماً سعرياً ⇒ المدقّق يرصده.

لا يستدعي ``engines.engine.run_full_analysis`` (مكلف/بطيء) — يُختبر الوصل عبر
زرع كاش القرص الذي تقرأه الدالة قبل اللجوء للمحرّك. الكاش يُوجَّه لمسار مؤقّت
حتى لا يُلامَس كاش التطبيق الحقيقي.
"""
from __future__ import annotations

import os
import sqlite3

import pandas as pd
import pytest

import conf.constants as constants
from bootstrap import (
    build_container,
    competitor_content_fingerprint,
    load_competitor_dfs,
    run_pricing_analysis,
)
from core.enums import SectionType
from services.missing_service import save_cache


@pytest.fixture
def temp_pricing_cache(tmp_path, monkeypatch):
    """يوجّه كاش التسعير لملف مؤقّت (يحمي كاش التطبيق الحقيقي)."""
    cache = tmp_path / "pricing_cache.json"
    monkeypatch.setattr(constants, "PRICING_CACHE_PATH", cache)
    return cache


def _make_db(path: str) -> None:
    """ينشئ ``competitor_products_store`` بصفّين لمتجرين مختلفين."""
    conn = sqlite3.connect(path)
    # يشمل الأعمدة الـ13 التي يقرؤها load_competitor_dfs بعد إسقاط SELECT *
    # (agg_name/extracted_*/product_line تبقى NULL هنا — تكفي لاختبار التسمية/التجميع).
    conn.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, product_name TEXT, brand TEXT, category TEXT, "
        "image_url TEXT, product_url TEXT, price REAL, competitor TEXT, "
        "agg_name TEXT, extracted_brand TEXT, extracted_size TEXT, "
        "extracted_gender TEXT, extracted_class TEXT, product_line TEXT)"
    )
    conn.executemany(
        "INSERT INTO competitor_products_store "
        "(product_name, brand, category, image_url, product_url, price, competitor) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("عطر أ 100 مل", "Dior", "عطور", "img1", "url1", 200.0, "متجر س"),
            ("عطر ب 50 مل", "Gucci", "عطور", "img2", "url2", 150.0, "متجر ص"),
            ("منتج بلا سعر", "X", "عطور", "", "", 0.0, "متجر س"),  # يُستبعد (price=0)
        ],
    )
    conn.commit()
    conn.close()


def test_load_competitor_dfs_renames_and_groups(tmp_path) -> None:
    db = tmp_path / "comp.db"
    _make_db(str(db))
    comp_dfs = load_competitor_dfs(str(db))
    assert set(comp_dfs.keys()) == {"متجر س", "متجر ص"}      # تجميع بالمتجر
    df_s = comp_dfs["متجر س"]
    assert "المنتج" in df_s.columns and "السعر" in df_s.columns   # إعادة التسمية
    assert "رابط المنتج" in df_s.columns and "صورة المنتج" in df_s.columns
    assert len(df_s) == 1                                    # السعر=0 مُستبعد


def test_load_competitor_dfs_missing_db_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_competitor_dfs(str(tmp_path / "nope.db"))


def _seed_pricing_cache(cache_path, our_len: int, use_ai: bool, results_df: pd.DataFrame) -> None:
    """يزرع كاش التسعير بتوقيع يطابق ما تبنيه ``run_pricing_analysis``."""
    db = str(constants.COMPETITOR_DB_PATH)
    fingerprint = competitor_content_fingerprint(db) if os.path.exists(db) else "0"
    sig = f"{constants.PRICING_CACHE_VERSION}|{our_len}|{fingerprint}|{int(bool(use_ai))}"
    save_cache(str(cache_path), sig, results_df)


def _balanced_results() -> pd.DataFrame:
    # «المنتج» = اسم منتجنا (يحمله مخرج المحرّك الحقيقي) — يطابق our_df في الاختبار
    # لتعمل شبكة أمان استرداد الكتالوج كما في الإنتاج (لا استرداد زائف).
    return pd.DataFrame({
        "القرار": ["🔴 سعر أعلى", "🟢 سعر أقل", "✅ موافق",
                   "⚠️ تحت المراجعة", "⚪ مستبعد"],
        "المنتج": [f"منتج {i}" for i in range(5)],
        "منتج_المنافس": ["عطر أ", "عطر ب", "عطر ج", "عطر د", "عطر هـ"],
        "السعر": [120, 90, 100, 0, 0],
        "سعر_المنافس": [100, 110, 100, 0, 0],
    })


def test_run_pricing_analysis_via_seeded_cache(temp_pricing_cache) -> None:
    """كاش مزروع ⇒ لا محرّك ثقيل؛ يتحقّق من الأقسام والتوازن والعدّ."""
    our_df = pd.DataFrame({"المنتج": [f"منتج {i}" for i in range(5)]})
    _seed_pricing_cache(temp_pricing_cache, len(our_df), False, _balanced_results())

    container = build_container()
    sections, result, _missing_clean, stats = run_pricing_analysis(
        container, our_df, use_ai=False, use_cache=True,
    )
    assert stats.get("cached") is True
    # توزيع صحيح على الأقسام الخمسة
    assert len(sections["price_raise"]) == 1
    assert len(sections["price_lower"]) == 1
    assert len(sections["approved"]) == 1
    assert len(sections["review"]) == 1
    assert len(sections["excluded"]) == 1
    # حفظ البيانات سليم
    assert result.reconciliation.is_balanced
    assert result.reconciliation.gap == 0
    assert result.total == 5
    assert result.section_counts[SectionType.PRICE_RAISE] == 1
    assert stats.get("catalog_recovered") == 0   # الكل مُغطّى ⇒ لا استرداد


def test_run_pricing_analysis_dedups_matched_from_missing(temp_pricing_cache) -> None:
    """المنافس المطابَق سعرياً يُزال من المفقودات (مصدر حقيقة واحد) ⇒ توازن سليم."""
    our_df = pd.DataFrame({"المنتج": ["a", "b"]})
    shared = "عطر مشترك نادر 100 مل"
    results = pd.DataFrame({
        "القرار": ["🔴 سعر أعلى"], "منتج_المنافس": [shared],
        "السعر": [300], "سعر_المنافس": [250],
    })
    _seed_pricing_cache(temp_pricing_cache, len(our_df), False, results)
    missing_df = pd.DataFrame({
        "منتج_المنافس": [shared, "عطر آخر مفقود"],
        "مستوى_الثقة": ["green", "review"],
    })
    container = build_container()
    _sections, result, missing_clean, stats = run_pricing_analysis(
        container, our_df, use_ai=False, use_cache=True, missing_df=missing_df,
    )
    assert stats["missing_deduped"] == 1                      # أُزيل المشترك
    assert list(missing_clean["منتج_المنافس"]) == ["عطر آخر مفقود"]
    assert result.section_counts[SectionType.MISSING] == 0    # المشترك (green) أُزيل
    # بعد التنقية لا تكرار ⇒ توازن سليم (هذا هو منطق app.py الحاسم)
    assert result.reconciliation.duplicate_count == 0
    assert result.reconciliation.is_balanced


def test_run_pricing_analysis_reloads_spilled_missing_after_cached_pricing(
    temp_pricing_cache, tmp_path,
) -> None:
    """المفقودات المسكوبة تُستعاد بنفس التنقية بعد اكتمال مسار التسعير."""
    our_df = pd.DataFrame({"المنتج": ["a", "b"]})
    shared = "عطر مشترك نادر 100 مل"
    results = pd.DataFrame({
        "القرار": ["🔴 سعر أعلى"], "منتج_المنافس": [shared],
        "السعر": [300], "سعر_المنافس": [250],
    })
    _seed_pricing_cache(temp_pricing_cache, len(our_df), False, results)
    spilled = pd.DataFrame({
        "منتج_المنافس": [shared, "عطر آخر مفقود"],
        "مستوى_الثقة": ["green", "review"],
    })
    spill_path = tmp_path / "missing.pkl"
    spilled.to_pickle(spill_path)

    container = build_container()
    _sections, result, missing_clean, stats = run_pricing_analysis(
        container,
        our_df,
        use_ai=False,
        use_cache=True,
        missing_df=None,
        missing_spill_path=str(spill_path),
    )

    assert stats["missing_deduped"] == 1
    assert list(missing_clean["منتج_المنافس"]) == ["عطر آخر مفقود"]
    assert result.reconciliation.duplicate_count == 0
    assert result.reconciliation.is_balanced
