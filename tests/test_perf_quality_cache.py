"""اختبارات كاش تقرير الجودة (تخفيف الأداء — الخطوة ١).

يضمن أن التخزين المؤقت لا يغيّر أي نتيجة (يطابق الحساب المباشر) وأنّ نفس البصمة
تُحسب مرّة واحدة فقط — دون لمس الدالة الخالصة ``quality_report_data``.
"""
import pandas as pd

from ui.pages import dashboard
from ui.pages.dashboard import (
    _catalog_fingerprint,
    _get_quality_cache,
    quality_report_data,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "المنتج": ["عطر أ", "عطر ب", "عطر ج"],
            "السعر": [100, 200, 300],
            "الماركة": ["X", "Y", "Z"],
        }
    )


def test_fingerprint_cheap_and_shape_sensitive():
    """البصمة = (عدد الصفوف، أسماء الأعمدة) — رخيصة وتتغيّر بتغيّر الشكل."""
    df = _sample_df()
    assert _catalog_fingerprint(df) == (3, ("المنتج", "السعر", "الماركة"))
    assert _catalog_fingerprint(None) == (0, ())
    assert _catalog_fingerprint(df.iloc[:2]) != _catalog_fingerprint(df)


def test_cache_equals_direct_computation():
    """ناتج الكاش يطابق الحساب المباشر تماماً (لا يغيّر أي قيمة)."""
    df = _sample_df()
    cache = _get_quality_cache()
    cache.clear()
    cached = cache(_catalog_fingerprint(df), df)
    direct = quality_report_data(df)
    assert cached == direct


def test_same_fingerprint_computes_once(monkeypatch):
    """استدعاءان بنفس البصمة ⇒ حساب واحد فقط (إعادة استخدام لا إعادة حساب)."""
    df = _sample_df()
    cache = _get_quality_cache()
    cache.clear()
    calls = {"n": 0}
    real = quality_report_data

    def counting(d):
        calls["n"] += 1
        return real(d)

    monkeypatch.setattr(dashboard, "quality_report_data", counting)
    fp = _catalog_fingerprint(df)
    r1 = cache(fp, df)
    r2 = cache(fp, df.copy())  # نفس البصمة، DataFrame مختلف (يُتجاهَل في الهاش)
    assert r1 == r2
    assert calls["n"] == 1
