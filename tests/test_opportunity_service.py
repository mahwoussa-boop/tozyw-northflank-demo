"""
tests/test_opportunity_service.py — اختبار «رادار الفرص» (الطبقة النقيّة)
========================================================================
يختبر الدرجة والترتيب عبر ``rank_opportunities`` **بلا لمس أي قاعدة بيانات**
(بلا أيّ SQL كتابة): صفوف مبنيّة يدوياً كقواميس. التأكيدات على الترتيب النسبي
والحدود البنيوية لا على قيم float مطلقة — متينة أمام تغيّر البيانات/الأوزان.
"""
from services.opportunity_service import (
    rank_opportunities,
    score_row,
    opportunity_to_card_row,
    Opportunity,
    W_STOCKOUT,
    W_DEMAND,
    W_RATING,
)
from ui.components.comparison_card import build_missing_card_html


def _row(norm_name, store_count, out_ratio, rating_avg=None,
         price_min=None, price_median=None, price_max=None,
         rating_count=0, in_our_catalog=0):
    """يبني صفّ تجميع خام كما يعيده جلب SQL (قاموس)."""
    return {
        "norm_name": norm_name,
        "store_count": store_count,
        "out_ratio": out_ratio,
        "price_min": price_min,
        "price_median": price_median,
        "price_max": price_max,
        "rating_avg": rating_avg,
        "rating_count": rating_count,
        "in_our_catalog": in_our_catalog,
    }


# ────────────────────────── (أ) تأكيدات بنيوية ──────────────────────────
def test_empty_input_returns_empty_list():
    assert rank_opportunities([]) == []


def test_structural_invariants():
    rows = [
        _row("a", store_count=8, out_ratio=0.5, rating_avg=4.2),
        _row("b", store_count=3, out_ratio=0.0, rating_avg=None),
        _row("c", store_count=12, out_ratio=1.0, rating_avg=3.0),
        _row("d", store_count=5, out_ratio=0.2, rating_avg=5.0, rating_count=40),
    ]
    ranked = rank_opportunities(rows)

    # لا فقدان صفوف
    assert len(ranked) == len(rows)
    for o in ranked:
        assert isinstance(o, Opportunity)
        # out_ratio ضمن [0,1]
        assert 0.0 <= o.out_ratio <= 1.0
        # store_count موجب
        assert o.store_count > 0
        # الدرجة ضمن [0,1] (كل الإشارات مطبّعة والأوزان مجموعها 1)
        assert 0.0 <= o.score <= 1.0


def test_list_is_sorted_descending_by_score():
    rows = [
        _row("a", store_count=8, out_ratio=0.5, rating_avg=4.2),
        _row("b", store_count=3, out_ratio=0.0),
        _row("c", store_count=12, out_ratio=1.0, rating_avg=3.0),
        _row("d", store_count=5, out_ratio=0.2, rating_avg=5.0),
    ]
    ranked = rank_opportunities(rows)
    scores = [o.score for o in ranked]
    assert scores == sorted(scores, reverse=True)


# ────────────────────────── (ب) تحقّق تجريبي ──────────────────────────
def test_high_demand_high_stockout_beats_weak_signal():
    """منتج بانتشار عالٍ ونفاد عالٍ يتفوّق على منتج ضعيف الإشارة (ولو عالي التقييم)."""
    strong = _row("strong", store_count=10, out_ratio=0.8, rating_avg=4.0)
    weak = _row("weak", store_count=3, out_ratio=0.0, rating_avg=5.0)

    ranked = rank_opportunities([weak, strong])  # عمداً بترتيب مقلوب
    assert ranked[0].norm_name == "strong"
    # الترتيب النسبي فقط — لا قيمة float مثبّتة
    strong_opp = next(o for o in ranked if o.norm_name == "strong")
    weak_opp = next(o for o in ranked if o.norm_name == "weak")
    assert strong_opp.score > weak_opp.score


def test_stockout_outweighs_pure_demand_tie():
    """عند تساوي الطلب، المنتج الأعلى نفاداً يتقدّم (W_STOCKOUT هو الأعلى)."""
    out = _row("out", store_count=6, out_ratio=0.9)
    instock = _row("instock", store_count=6, out_ratio=0.0)
    ranked = rank_opportunities([instock, out])
    assert ranked[0].norm_name == "out"


def test_in_our_catalog_does_not_affect_score():
    """in_our_catalog حقل عرض فقط — لا يغيّر الدرجة إطلاقاً."""
    base = dict(store_count=7, out_ratio=0.4, rating_avg=4.0)
    yes = _row("x", in_our_catalog=1, **base)
    no = _row("x", in_our_catalog=0, **base)
    # نفس max_store_count لكليهما
    s_yes = score_row(yes, max_store_count=7)
    s_no = score_row(no, max_store_count=7)
    assert s_yes == s_no


def test_weights_ordering_contract():
    """عقد التصميم: وزن النفاد هو الأعلى ثم الطلب ثم التقييم."""
    assert W_STOCKOUT > W_DEMAND > W_RATING


# ────────────── (ج) جسر البطاقة: opportunity_to_card_row (نقيّ) ──────────────
def _opp(**kw) -> Opportunity:
    """Opportunity نموذجي (كل الحقول المطلوبة) مع تجاوزات اختيارية."""
    base = dict(
        norm_name="عطر لطافة", store_count=5, out_ratio=0.6,
        price_min=90.0, price_median=110.0, price_max=130.0,
        instock_price_min=100.0, rating_avg=4.2, rating_count=8,
        in_our_catalog=False, suggested_price=99.0, score=0.7,
        data_age_days=1.0, image_url="https://x/a.jpg", product_url="https://x/p",
    )
    base.update(kw)
    return Opportunity(**base)


def test_card_row_keys_values_and_render():
    r = opportunity_to_card_row(_opp())
    assert r["منتج_المنافس"] == "عطر لطافة" and r["سعر_المنافس"] == 100.0
    assert r["السعر_المقترح"] == 99.0 and r["رابط_المنافس"] == "https://x/p"
    assert r["عدد_المنافسين"] == 5 and r["المنافس"] == "5 متجر"
    assert r["الماركة"] == "" and r["مستوى_الثقة"] == "" and r["ownership"] == ""
    assert (r["score"], r["out_ratio"], r["data_age_days"]) == (0.7, 0.6, 1.0)
    assert "mhw-card" in build_missing_card_html(r)   # يتدفّق للبطاقة بلا استثناء


def test_card_row_price_fallback_and_image_none_safe():
    r = opportunity_to_card_row(
        _opp(instock_price_min=None, image_url=None, product_url=None))
    assert r["سعر_المنافس"] == 90.0                    # لا متوفّر ⇒ أدنى سعر
    assert r["صورة_المنافس"] is None and r["رابط_المنافس"] == ""
    assert "mhw-card" in build_missing_card_html(r)    # لا استثناء رغم None
