"""اختبارات كاش فحص الماركات (تخفيف الأداء — الخطوة ٢).

يضمن أن التخزين المؤقت لا يغيّر ناتج ``check_and_prepare_brands`` وأنّ حلقة O(n)
تُحسب مرّة واحدة لكل بصمة قسم.
"""
import pandas as pd
import streamlit as st

import services.brand_manager as bm
from ui.components.action_bar import _get_brand_check_cache, _section_fingerprint


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "الماركة": ["ماركة_وهمية_زد_غير_معروفة", "Chanel"],
            "المنتج": ["منتج أ", "منتج ب"],
        }
    )


def test_section_fingerprint_cheap_and_shape_sensitive():
    df = _df()
    assert _section_fingerprint(df) == (2, ("الماركة", "المنتج"))
    assert _section_fingerprint(None) == (0, ())
    assert _section_fingerprint(df.iloc[:1]) != _section_fingerprint(df)


def test_cache_equals_direct_computation():
    """ناتج الكاش يطابق الاستدعاء المباشر تماماً."""
    df = _df()
    cache = _get_brand_check_cache(st)
    cache.clear()
    cached = cache(_section_fingerprint(df), df)
    direct = bm.check_and_prepare_brands(df.to_dict("records"))
    assert cached == direct


def test_loop_computes_once_per_fingerprint(monkeypatch):
    """الحلقة تُحسب مرّة واحدة لكل بصمة قسم (لا تُعاد كل تفاعل)."""
    df = _df()
    cache = _get_brand_check_cache(st)
    cache.clear()
    calls = {"n": 0}
    real = bm.check_and_prepare_brands

    def counting(products):
        calls["n"] += 1
        return real(products)

    monkeypatch.setattr(bm, "check_and_prepare_brands", counting)
    fp = _section_fingerprint(df)
    r1 = cache(fp, df)
    r2 = cache(fp, df.copy())  # نفس البصمة، DataFrame مختلف (يُتجاهَل في الهاش)
    assert r1 == r2
    assert calls["n"] == 1
