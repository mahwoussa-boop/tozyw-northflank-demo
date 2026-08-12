"""شبكة أمان _flush في run_full_analysis — لا يُسقط منتجنا صامتاً.

مسار AI الرمادي (score 60–84 + use_ai) يمرّ عبر _flush. كان استثناء داخله أو
إعادة _row لقيمة None يُسقط منتجنا من النتائج (خلافاً لمسار ≥85 المحمي بشبكة
row_returned_none). هذان الاختباران يوجّهان منتجاً واحداً حتمياً إلى _flush
ويُجبران الفشل، ويتحقّقان أن المنتج يبقى صفّاً «مستبعداً» مرئياً (لا فقدان بيانات).

حتمية بلا شبكة/عشوائية: نُبدّل _search_indices (مرشّح رمادي ثابت 70)، و_ai_batch
(بلا استدعاء AI)، و_row (يفشل عمداً).
"""
import pandas as pd

import engines.engine as eng
from engines.engine import run_full_analysis

_PNAME = "عطر تجريبي للاختبار 100 مل"


def _our_df():
    return pd.DataFrame({"اسم المنتج": [_PNAME], "السعر": [200.0]})


def _comp_dfs():
    return {"CompA": pd.DataFrame({"اسم المنتج": ["منتج منافس"], "السعر": [150.0]})}


def _gray_candidate(*_a, **_k):
    # مرشّح واحد في النطاق الرمادي (60–84) وبماركة فارغة كي لا تُنقّيه فلترة
    # خط الإنتاج → يُوجَّه إلى _flush حين use_ai=True.
    return [{"name": "منتج منافس", "score": 70.0, "price": 150.0,
             "competitor": "CompA", "product_url": "", "brand": ""}]


def _route_to_flush(monkeypatch):
    monkeypatch.setattr(eng, "_search_indices", _gray_candidate)
    monkeypatch.setattr(eng, "_ai_batch", lambda batch: [-1])  # AI غير متأكد، بلا شبكة


def test_flush_exception_keeps_product(monkeypatch):
    """استثناء داخل _flush ⇒ المنتج يبقى صفّاً مستبعداً بمصدر flush_row_error."""
    _route_to_flush(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("فشل مُصطنع داخل _flush")

    monkeypatch.setattr(eng, "_row", _boom)

    df, audit = run_full_analysis(_our_df(), _comp_dfs(), use_ai=True)

    assert len(df) == 1, "المنتج فُقد بدل حفظه كصفّ مستبعد"
    assert (df["المنتج"] == _PNAME).any()
    assert (df["مصدر_المطابقة"] == "flush_row_error").any()
    assert audit.get("dropped_flush_error", 0) == 1


def test_flush_none_keeps_product(monkeypatch):
    """_row يعيد None داخل _flush ⇒ المنتج يبقى صفّاً مستبعداً (تناظر مسار ≥85)."""
    _route_to_flush(monkeypatch)
    monkeypatch.setattr(eng, "_row", lambda *_a, **_k: None)

    df, audit = run_full_analysis(_our_df(), _comp_dfs(), use_ai=True)

    assert len(df) == 1, "المنتج فُقد بدل حفظه كصفّ مستبعد"
    assert (df["المنتج"] == _PNAME).any()
    assert (df["مصدر_المطابقة"] == "row_returned_none").any()
    assert audit.get("dropped_none", 0) == 1
