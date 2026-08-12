"""توازي دُفعات AI الحقيقي في run_full_analysis (بند و — 2026-07-22).

المشكلة السابقة: كل دفعة AI (BATCH=30) كانت تُرسَل وتُنتظَر (blocking) داخل
الحلقة الرئيسية تسلسلياً رغم استيراد ThreadPoolExecutor بلا استخدام. الإصلاح:
كل دفعة تُرسَل لخيط خلفي فور امتلائها (لا حجب)، وتُجمَع النتائج في نهاية
الحلقة بترتيب **الإرسال** الأصلي حصراً (لا ترتيب الإنجاز) — هذا الاختبار
يثبت أن الترتيب لا يتغيّر حتى لو أنجزت دفعة لاحقة قبل دفعة سابقة فعلياً.
"""
import time

import pandas as pd

import engines.engine as eng
from engines.engine import run_full_analysis


def _gray_candidate(*_a, **_k):
    # مرشّح واحد في النطاق الرمادي (60–84) لكل منتج ⇒ كل الصفوف تمر عبر AI/_flush.
    return [{"name": "منتج منافس", "score": 70.0, "price": 150.0,
             "competitor": "CompA", "product_url": "", "brand": ""}]


def test_flush_preserves_row_order_across_concurrent_batches(monkeypatch):
    """ترتيب results عبر عدة دُفعات AI متزامنة يطابق ترتيب المدخل الأصلي
    حتى لو أنجزت دفعة لاحقة قبل دفعة سابقة (I/O متداخل حقيقي)."""
    n = 65  # BATCH=30 ⇒ 3 دُفعات: 30 + 30 + 5
    # "- 100 مل" ثابت مفصول عن الرقم التسلسلي كي لا يلتقطه فلتر العيّنات
    # الصغيرة (size_ml بين 0-2) عرَضاً فيُخرج منتجاً من مسار AI بلا علاقة بما نختبره.
    names = [f"عطر تجريبي رقم {i:03d} - 100 مل" for i in range(n)]
    our_df = pd.DataFrame({"اسم المنتج": names, "السعر": [200.0] * n})
    comp_dfs = {"CompA": pd.DataFrame({"اسم المنتج": ["منتج منافس"], "السعر": [150.0]})}

    monkeypatch.setattr(eng, "_search_indices", _gray_candidate)

    call_order: list[int] = []

    def _slow_ai_batch(batch):
        # الدفعة الأولى المُرسَلة تنام أطول من اللاحقتين، فلو استُبدل ترتيب
        # الإرسال بترتيب الإنجاز لظهرت الدفعة الثانية/الثالثة أولاً في النتائج.
        call_order.append(len(batch))
        time.sleep(0.3 if len(call_order) == 1 else 0.02)
        return [-1] * len(batch)  # كل العناصر "تحت المراجعة" — لا حاجة لمطابقة حقيقية

    monkeypatch.setattr(eng, "_ai_batch", _slow_ai_batch)

    df, _audit = run_full_analysis(our_df, comp_dfs, use_ai=True)

    assert call_order == [30, 30, 5]  # ثلاث دُفعات كما هو متوقَّع
    assert list(df["المنتج"]) == names, "توازي الشبكة بدّل ترتيب النتائج"
