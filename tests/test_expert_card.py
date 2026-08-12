"""tests/test_expert_card.py — اختبارات عقل بطاقة الخبير الخالص (بلا Streamlit).

يختبر الطبقتين النقيّتين: ``analyze`` (الاشتقاق/التدقيق/الفرز) و
``build_expert_card_html`` (سلامة الـHTML الناتج). الغلاف ``render_expert_card``
لا يُختبَر هنا (يحتاج Streamlit) — وهذا مقصود: المنطق كله خارج الغلاف.
"""
from ui.components.expert_card import (
    analyze,
    audit_competitors,
    build_expert_card_html,
    demo_data,
    row_to_expert,
)


# ── التدقيق: استبعاد التستر/الشاذ آلياً ──
def test_auditor_flags_tester_outlier() -> None:
    comps = [
        {"store": "أ", "price": 700, "score": 95},
        {"store": "ب", "price": 690, "score": 94},
        {"store": "ج", "price": 705, "score": 93},
        {"store": "تستر", "price": 200, "score": 88},  # < 45% من الوسيط ⇒ يُستبعد
    ]
    audited = audit_competitors(comps)
    excluded = [c for c in audited if c["excluded"]]
    assert len(excluded) == 1
    assert excluded[0]["store"] == "تستر"
    assert excluded[0]["is_tester"] is True
    assert excluded[0]["reason"]  # سبب غير فارغ


def test_auditor_respects_explicit_exclude() -> None:
    comps = [{"store": "x", "price": 100, "excluded": True,
              "exclude_reason": "تصفية مخزون"}]
    audited = audit_competitors(comps)
    assert audited[0]["excluded"] is True
    assert audited[0]["reason"] == "تصفية مخزون"


def test_auditor_skips_when_too_few() -> None:
    # أقل من 3 منافسين ⇒ لا كشف آلي (حتى لو بدا أحدهم شاذاً).
    audited = audit_competitors([{"store": "a", "price": 700},
                                 {"store": "b", "price": 100}])
    assert all(not c["excluded"] for c in audited)


# ── الاشتقاق: الأرضية الموثوقة ≠ أرخص سعر خام ──
def test_trusted_floor_excludes_outlier_from_market() -> None:
    data = {
        "product": {"name": "P"}, "our_price": 720, "cost": 520,
        "competitors": [
            {"store": "أ", "price": 700, "score": 95},
            {"store": "ب", "price": 690, "score": 94},
            {"store": "ج", "price": 705, "score": 93},
            {"store": "تستر", "price": 200, "score": 88},
        ],
    }
    m = analyze(data)
    assert m["raw_low"] == 200          # أرخص سعر خام يشمل التستر
    assert m["trusted_low"] == 690      # الأرضية الموثوقة تتجاهله
    assert m["market_avg"] > 600        # المتوسط لم يتلوّث بالتستر
    assert len(m["excluded"]) == 1


# ── الاستراتيجي: لا ينزل تحت أرضية الربح أبداً ──
def test_strategist_never_breaks_margin_floor() -> None:
    data = {
        "product": {"name": "P"}, "our_price": 300, "cost": 280,
        "min_margin_pct": 15,  # أرضية = 280 * 1.15 = 322
        "competitors": [
            {"store": "أ", "price": 250, "score": 96, "authority": 1.0},
            {"store": "ب", "price": 255, "score": 95},
            {"store": "ج", "price": 248, "score": 94},
        ],
    }
    m = analyze(data)
    assert m["margin_floor"] == 322.0
    assert m["suggested"] >= m["margin_floor"]  # الربح أولوية
    assert m["margin_ok"] is True


def test_provided_suggested_price_overrides() -> None:
    data = {
        "product": {"name": "P"}, "our_price": 135, "cost": 78,
        "suggested_price": 129,
        "competitors": [{"store": "أ", "price": 149, "score": 98}],
    }
    assert analyze(data)["suggested"] == 129.0


# ── الفرز التلقائي ──
def test_triage_green_for_high_match_safe_margin() -> None:
    data = demo_data()[0]  # لطافة خمرة — مصمَّمة لتكون خضراء
    m = analyze(data)
    assert m["triage"]["key"] == "green"


def test_triage_yellow_on_sharp_drop() -> None:
    data = demo_data()[1]  # ديور — هبوط سعري حادّ
    m = analyze(data)
    assert m["triage"]["key"] == "yellow"


def test_triage_red_on_weak_match() -> None:
    data = {
        "product": {"name": "P"}, "our_price": 100,
        "competitors": [{"store": "أ", "price": 95, "score": 40}],
    }
    assert analyze(data)["triage"]["key"] == "red"


def test_confidence_from_identifier_swarm() -> None:
    data = {
        "product": {"name": "P"}, "our_price": 100,
        "swarm": {"identifier": {"confidence": 87}},
        "competitors": [{"store": "أ", "price": 95, "score": 50}],
    }
    assert analyze(data)["confidence"] == 87  # يفضّل المعرّف على score الخام


# ── سلامة الـHTML ──
def test_html_is_single_line_no_blank_leak() -> None:
    html = build_expert_card_html(demo_data()[0])
    assert "\n" not in html                 # حارس الأسطر الفارغة فعّال
    assert html.startswith("<div class=\"xc-card")
    assert html.count("xc-card") == 1


def test_html_renders_all_zones() -> None:
    html = build_expert_card_html(demo_data()[2])  # حالة فيها تستر مستبعَد
    for token in ("xc-ribbon", "xc-head", "xc-kpis", "xc-ladder",
                  "xc-radar", "xc-row-img", "xc-row-nmt", "xc-verdict", "🎯", "👑"):
        assert token in html, f"المنطقة/الرمز مفقود: {token}"
    assert "xc-row-ex" in html  # ظهر المنافس المستبعَد مشطوباً في الرادار


def test_html_handles_empty_competitors() -> None:
    html = build_expert_card_html(demo_data()[3])  # بلا منافسين
    assert "xc-card" in html
    assert "منتج منفرد" in html  # رسالة الرادار الفارغ


def test_all_demo_cards_build_without_error() -> None:
    for item in demo_data():
        html = build_expert_card_html(item)
        assert html and "xc-card" in html


# ── المحوّل: صف نتيجة المحرّك الحقيقي → مدخلات البطاقة ──
def test_row_to_expert_from_engine_pricing_row() -> None:
    from conf.constants import (
        COL_BRAND, COL_OUR_ID, COL_OUR_NAME, COL_OUR_PRICE, COL_SIZE,
        COL_SUGGESTED_PRICE, COL_TYPE,
    )
    row = {
        COL_OUR_NAME: "Lattafa Khamrah EDP", COL_OUR_ID: "20988",
        COL_OUR_PRICE: 135, COL_BRAND: "لطافة", COL_SIZE: "100",
        COL_TYPE: "EDP", "التكلفة": 78, COL_SUGGESTED_PRICE: 132,
        "صورة_منتجنا": "https://x/our.jpg",
        # مسار التسعير: قائمة بمفاتيح إنجليزية (يفهمها parse_competitor_details).
        "جميع_المنافسين": [
            {"competitor": "نايس ون", "name": "لطافة خمرة", "price": 149,
             "image_url": "https://x/1.jpg", "product_url": "https://niceonesa.com",
             "size": "100", "score": 98},
            {"competitor": "أمانة", "name": "Lattafa Khamrah", "price": 142,
             "image_url": "", "product_url": "", "size": "100", "score": 95},
        ],
    }
    data = row_to_expert(row)
    assert data["product"]["name"] == "Lattafa Khamrah EDP"
    assert data["product"]["brand"] == "لطافة"
    assert data["our_price"] == 135
    assert data["cost"] == 78
    assert data["suggested_price"] == 132
    assert len(data["competitors"]) == 2
    assert all(c["store"] for c in data["competitors"])

    m = analyze(data)
    assert m["suggested"] == 132        # احترم سعر المحرّك (لم يُعد حسابه)
    assert m["confidence"] >= 95        # اشتُقّت من أعلى score
    html = build_expert_card_html(data)
    assert "نايس ون" in html and "xc-card" in html


def test_row_to_expert_handles_sparse_row() -> None:
    from conf.constants import COL_OUR_NAME, COL_OUR_PRICE
    data = row_to_expert({COL_OUR_NAME: "منتج بلا منافسين", COL_OUR_PRICE: 200})
    assert data["our_price"] == 200
    assert data["competitors"] == []
    assert analyze(data)["triage"]["key"] == "solo"   # بلا سوق موثوق
    assert build_expert_card_html(data)               # لا ينهار
