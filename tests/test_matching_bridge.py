# -*- coding: utf-8 -*-
"""اختبارات جسر المطابقة v35 — انحدار لعطلَي «4,196 مستبعد» (2026-07-11).

الخطأ الأول: حارس الفلانكرز قارن اسمنا المطبَّع ضد اسم المنافس الخام،
فكلمة «اورا» (مجموعة Aura) تُكتشف داخل «اوراق» المطبَّعة عندنا ولا تُكتشف
في «أوراق» الخام (بالهمزة) عند المنافس ⇒ رفض التوأم المطابق 100%.

الخطأ الثاني: حلقة التحليل كانت تشتق ماركة البحث من extract_brand(الاسم)
فقط وتتجاهل عمود «الماركة» — فأي ماركة خارج KNOWN_BRANDS (روسيندو ماتيو…)
⇒ صفر مرشحين ⇒ «مستبعد (لا يوجد تطابق)» رغم بيع المنافسين لها.
"""
import pandas as pd
import pytest

from engines.engine import (
    CompIndex,
    _brand_for_pline,
    _our_brand_candidates,
    _search_indices,
    extract_brand,
    extract_gender,
    extract_size,
    extract_type,
    match_single_product,
    normalize,
)


def _mk_index(rows, comp="متجر الاختبار"):
    """فهرس منافس من صفوف (اسم، سعر، ماركة) — بلا أعمدة استخراج مسبق."""
    df = pd.DataFrame(
        [{"id": i + 1, "المنتج": n, "السعر": p, "brand": b} for i, (n, p, b) in enumerate(rows)]
    )
    return {comp: CompIndex(df, "المنتج", "id", comp)}


# ─────────────────────────────────────────────────────────────
#  الخطأ الأول: تناظر حارس الفلانكرز (مطبَّع ↔ مطبَّع)
# ─────────────────────────────────────────────────────────────

def test_flanker_guard_matches_identical_twin_with_hamza():
    """«أوراق» بالهمزة عند المنافس يجب ألا تُفشل مطابقة توأم حرفي."""
    product = "عطر لطافة أوراق الفضة أو دو برفيوم 100 مل"
    indices = _mk_index([(product, 63.25, "لطافة")])
    res = indices["متجر الاختبار"].search(
        normalize(product), extract_brand(product),
        extract_size(product), extract_type(product), extract_gender(product),
        our_pline="اوراق الفضه", top_n=6, our_price=69.0,
    )
    assert len(res) == 1, "التوأم المطابق حرفياً يجب أن يُطابَق"
    assert res[0]["score"] >= 95


def test_flanker_guard_still_rejects_real_flanker_mismatch():
    """الحارس يبقى صارماً: سبورت مقابل غير سبورت لنفس الماركة ⇒ رفض."""
    ours = "عطر لطافة قمة أو دو برفيوم 100 مل"
    comp = "عطر لطافة قمة سبورت أو دو برفيوم 100 مل"
    indices = _mk_index([(comp, 99.0, "لطافة")])
    res = indices["متجر الاختبار"].search(
        normalize(ours), extract_brand(ours),
        extract_size(ours), extract_type(ours), extract_gender(ours),
        our_pline="قمه", top_n=6, our_price=0,
    )
    assert res == [], "اختلاف مجموعة الفلانكرز لنفس الماركة يجب أن يبقى رفضاً"


# ─────────────────────────────────────────────────────────────
#  الخطأ الثاني: جسر مفاتيح الماركة (عمود سلة «عربي | English»)
# ─────────────────────────────────────────────────────────────

def test_brand_candidates_dedup_and_order():
    keys = _our_brand_candidates("Lattafa", "لطافة | Latafa")
    assert keys[0] == "Lattafa"
    # «لطافة» تُطبَّع إلى نفس مفتاح Lattafa فلا تتكرر
    norm_keys = [normalize(k).lower() for k in keys]
    assert len(norm_keys) == len(set(norm_keys))


def test_brand_candidates_unknown_brand_falls_back_to_column():
    keys = _our_brand_candidates("", "روسيندو ماتيو | Rosendo Mateu")
    assert keys, "عمود الماركة المملوء يجب أن يولّد مفاتيح بحث"
    assert "روسيندو ماتيو | Rosendo Mateu" in keys
    assert any(k.strip() == "روسيندو ماتيو" for k in keys)


def test_brand_for_pline_picks_key_present_in_name():
    product = "عطر روسيندو ماتيو رقم 2 أو دو بارفيوم 100مل"
    keys = _our_brand_candidates("", "روسيندو ماتيو | Rosendo Mateu")
    hint = _brand_for_pline(product, "", keys)
    assert normalize(hint) and normalize(hint).lower() in normalize(product).lower()
    # مع ماركة مستخرَجة تُعاد هي نفسها
    assert _brand_for_pline(product, "Lattafa", keys) == "Lattafa"


def test_unknown_brand_product_matches_via_column_bridge():
    """روسيندو ماتيو خارج KNOWN_BRANDS: قبل الجسر صفر مرشحين، بعده يُطابَق."""
    ours = "عطر روسيندو ماتيو نمبر 1 او دو بارفيوم 100 مل للجنسين"
    comp = "عطر روسيندو ماتيو نمبر 1 او دو بارفيوم 100 مل"
    assert extract_brand(ours) == "", "الاختبار يفترض ماركة خارج KNOWN_BRANDS"
    indices = _mk_index([(comp, 420.0, "روسيندو ماتيو")])

    row = match_single_product(
        product=ours, our_price=450.0, our_id="X1",
        brand=extract_brand(ours), display_brand="روسيندو ماتيو | Rosendo Mateu",
        size=extract_size(ours), ptype=extract_type(ours), gender=extract_gender(ours),
        our_img="", our_url="", indices=indices, use_ai=False,
    )
    assert row["مصدر_المطابقة"] != "no_candidates", "جسر العمود يجب أن يجلب مرشحين"
    assert row["عدد_المنافسين"] >= 1
    assert row["سعر_المنافس"] == pytest.approx(420.0)


def test_search_indices_dedups_same_row_across_keys():
    """الصف الواحد قد يصل من مفتاحين (مزدوج/مفرد) — يُحتسب مرة واحدة."""
    comp = "عطر روسيندو ماتيو نمبر 1 او دو بارفيوم 100 مل"
    indices = _mk_index([(comp, 420.0, "روسيندو ماتيو")])
    ours = "عطر روسيندو ماتيو نمبر 1 او دو بارفيوم 100 مل"
    keys = ["روسيندو ماتيو", "روسيندو ماتيو "]  # مفتاحان يطبَّعان لنفس الدلو
    res = _search_indices(
        indices, normalize(ours), keys,
        extract_size(ours), extract_type(ours), extract_gender(ours),
        our_pline="نمبر 1", top_n=6, our_price=0,
    )
    names = [(c["competitor"], c["name"]) for c in res]
    assert len(names) == len(set(names)), "لا تكرار لنفس (منافس، اسم)"


def test_known_brand_path_unchanged():
    """المسار الصحّي (ماركة معروفة في الاسم) يبقى كما هو: مفتاح أول = المستخرَجة."""
    product = "عطر لطافة تحفة الملوك أو دو برفيوم 80 مل"
    keys = _our_brand_candidates(extract_brand(product), "لطافة | Latafa")
    assert keys[0] == extract_brand(product) == "Lattafa"
