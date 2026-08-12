# -*- coding: utf-8 -*-
"""انحدار v36 — مطابقة مجموعة↔مجموعة مع بقاء رفض مجموعة↔عطر فردي.

العطب (تدقيق 2026-07-12): منتجاتنا «المجموعات/الأطقم» كانت تُستبعَد قبل أي
مطابقة، ولو أصلحنا البوابة العليا لبقي حارسان داخليان يرفضان التوأم الشرعي
لأن normalize يحوّل «مجموعة»→«مgوعه» فيخطئ كشف المجموعة/التصنيف على جهتنا
المطبَّعة بينما ينجح على المنافس الخام. مثال حيّ: «عطر لطافة مجموعة المسك
الشرقي» (172.5) له توأم مطابق حرفياً عند «عالم جيفنشي» ومع ذلك مستبعَد.

الإصلاح: كشف «مجموعة» متناظر على النص المطبَّع للطرفين (‎_SET_KW_NORM‎) —
مجموعة↔مجموعة تمرّ، ومجموعة↔عطر فردي تبقى مرفوضة (فلترا التصنيف والحارس).
"""
import pandas as pd
import pytest

from engines.engine import (
    CompIndex,
    extract_brand,
    extract_gender,
    extract_size,
    extract_type,
    match_single_product,
)


def _mk_index(rows, comp="متجر الاختبار"):
    """فهرس منافس من صفوف (اسم، سعر، ماركة) — بلا أعمدة استخراج مسبق."""
    df = pd.DataFrame(
        [{"id": i + 1, "المنتج": n, "السعر": p, "brand": b} for i, (n, p, b) in enumerate(rows)]
    )
    return {comp: CompIndex(df, "المنتج", "id", comp)}


def _match(ours, indices, price=172.5, display_brand=None):
    return match_single_product(
        product=ours, our_price=price, our_id="X1",
        brand=extract_brand(ours),
        display_brand=display_brand if display_brand is not None else extract_brand(ours),
        size=extract_size(ours), ptype=extract_type(ours), gender=extract_gender(ours),
        our_img="", our_url="", indices=indices, use_ai=False,
    )


def test_set_matches_identical_set_twin():
    """التوأم الحيّ: مجموعة لطافة المسك الشرقي تُطابَق نظيرتها الحرفية (كانت مستبعَدة)."""
    name = "عطر لطافة مجموعة المسك الشرقي"
    indices = _mk_index([(name, 172.5, "لطافة")])
    row = _match(name, indices, price=172.5, display_brand="لطافة")
    assert row["مصدر_المطابقة"] not in ("no_candidates", "below_match_threshold", "excluded_set_product"), \
        "مجموعة↔مجموعة مطابقة حرفياً يجب أن تُطابَق"
    assert row["عدد_المنافسين"] >= 1
    assert row["سعر_المنافس"] == pytest.approx(172.5)


def test_set_does_not_match_individual_perfume():
    """الحارس يبقى صارماً: مجموعتنا لا تُطابق عطراً فردياً لنفس الماركة/الخط."""
    ours = "عطر لطافة مجموعة المسك الشرقي"
    comp_individual = "عطر لطافة المسك الشرقي أو دو برفيوم 100 مل"
    indices = _mk_index([(comp_individual, 90.0, "لطافة")])
    row = _match(ours, indices, price=172.5, display_brand="لطافة")
    assert row["مصدر_المطابقة"] == "no_candidates", \
        "مجموعة↔عطر فردي يجب أن تبقى مرفوضة (لا SKU مطابق)"


def test_individual_does_not_match_set():
    """الاتجاه المعاكس: عطرنا الفردي لا يُطابق مجموعة عند المنافس."""
    ours = "عطر لطافة المسك الشرقي أو دو برفيوم 100 مل"
    comp_set = "عطر لطافة مجموعة المسك الشرقي"
    indices = _mk_index([(comp_set, 172.5, "لطافة")])
    row = _match(ours, indices, price=110.0, display_brand="لطافة")
    assert row["مصدر_المطابقة"] == "no_candidates", \
        "عطر فردي↔مجموعة يجب أن تبقى مرفوضة"
