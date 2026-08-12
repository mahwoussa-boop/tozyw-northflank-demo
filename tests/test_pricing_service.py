"""tests/test_pricing_service.py — اختبارات القرار السعري (P2)."""
import pytest

from core.enums import ActionType
from services.pricing_service import (
    DECISION_APPROVED,
    DECISION_LOWER,
    DECISION_NO_COMP_PRICE,
    DECISION_RAISE,
    PricingService,
    decide_price,
    smart_price_threshold,
    suggested_price_with_margin,
    undercut_price,
)


def test_smart_threshold_tiers() -> None:
    assert smart_price_threshold(500, 500) == 500 * 0.05   # متوسط ≥300 ⇒ 5%
    assert smart_price_threshold(150, 150) == 5            # متوسط [100,300) ⇒ الافتراضي
    assert smart_price_threshold(50, 50) == 5              # متوسط <100 ⇒ تسامح منخفض
    assert smart_price_threshold(0, 100) == 5             # سعر مفقود ⇒ الافتراضي


def test_decide_price_branches() -> None:
    assert decide_price(120, 100).decision == DECISION_RAISE     # نحن أعلى
    assert decide_price(100, 120).decision == DECISION_LOWER     # نحن أقل
    assert decide_price(102, 100).decision == DECISION_APPROVED  # ضمن التسامح
    assert decide_price(0, 100).decision == DECISION_NO_COMP_PRICE


def test_decide_price_diff_sign() -> None:
    d = decide_price(120, 100)
    assert d.diff == 20.0 and d.action == ActionType.RAISE


def test_suggested_price_helpers() -> None:
    assert undercut_price(100) == 99.0
    assert undercut_price(0) == 0.0
    assert suggested_price_with_margin(100, 20) == 120.0


def test_pricing_service_builds_price_diff() -> None:
    decision, diff = PricingService().evaluate("SKU9", our_price=120, comp_price=100)
    assert decision.action == ActionType.RAISE
    assert diff.product_id == "SKU9"
    assert diff.diff == 20.0
    assert diff.suggested_price == 99.0          # يُقترح خفض السعر تحت المنافس
    assert diff.diff_pct == 20.0


# ── اختبارات v2: التسعير الذكي ─────────────────────────────────────

from services.pricing_service import (
    CompetitorPriceInfo,
    PricingCache,
    PricingContext,
    SmartPricingEngine,
)


def test_competitor_price_info_from_dict() -> None:
    info = CompetitorPriceInfo.from_dict({"store_a": 100, "store_b": 120, "store_c": 0})
    assert info.count == 2
    assert info.min_price == 100
    assert info.max_price == 120
    assert info.avg_price == 110
    assert info.cheapest_store == "store_a"


def test_smart_pricing_engine_aggregate() -> None:
    engine = SmartPricingEngine()
    prices = {"a": 100, "b": 120, "c": 110}
    assert engine.aggregate_competitor_prices(prices, "min") == 100
    assert engine.aggregate_competitor_prices(prices, "max") == 120
    assert engine.aggregate_competitor_prices(prices, "avg") == 110
    assert engine.aggregate_competitor_prices(prices, "median") == 110
    # weighted = min*0.7 + avg*0.3 = 100*0.7 + 110*0.3 = 103
    assert engine.aggregate_competitor_prices(prices, "weighted") == 103.0


def test_smart_pricing_engine_suggested_price() -> None:
    engine = SmartPricingEngine()
    ctx = PricingContext(
        our_price=150,
        cost_price=80,
        competitor_info=CompetitorPriceInfo.from_dict({"store_x": 120}),
    )
    suggested, reason = engine.compute_suggested_price(ctx)
    assert suggested > 0
    assert "تقويض" in reason or "مطابقة" in reason or "هامش" in reason


def test_smart_pricing_engine_margin_floor() -> None:
    engine = SmartPricingEngine()
    ctx = PricingContext(
        our_price=150,
        cost_price=130,
        competitor_info=CompetitorPriceInfo.from_dict({"store_x": 100}),
    )
    suggested, reason = engine.compute_suggested_price(ctx)
    # يجب أن لا يقل السعر المقترح عن أرضية الربح
    floor = engine._price_floor(130)
    assert suggested >= floor


def test_smart_pricing_engine_ceiling() -> None:
    engine = SmartPricingEngine()
    ctx = PricingContext(
        our_price=200,
        competitor_info=CompetitorPriceInfo.from_dict({"store_x": 100}),
    )
    suggested, _ = engine.compute_suggested_price(ctx)
    ceiling = engine._price_ceiling(200, 100)
    assert ceiling is not None
    assert suggested <= ceiling


def test_smart_pricing_engine_strategy_variants() -> None:
    engine = SmartPricingEngine()
    for strategy in ("aggressive", "moderate", "premium", "erratic"):
        ctx = PricingContext(
            our_price=150,
            competitor_info=CompetitorPriceInfo.from_dict({"s": 120}),
            competitor_strategy=strategy,
        )
        d = engine.smart_decide(ctx)
        assert d.action is not None


def test_pricing_cache_ttl() -> None:
    cache = PricingCache(ttl_sec=1)
    cache.set("sku1", 100, {"comp": 90}, ("decision", "diff"))
    assert cache.get("sku1", 100, {"comp": 90}) == ("decision", "diff")
    assert cache.size() == 1
    cache.clear()
    assert cache.size() == 0


def test_pricing_service_smart_interface() -> None:
    svc = PricingService()
    ctx = PricingContext(
        product_id="SKU10",
        our_price=150,
        competitor_info=CompetitorPriceInfo.from_dict({"store_a": 120}),
    )
    decision, diff = svc.evaluate_smart(ctx)
    assert decision.action is not None
    assert diff.product_id == "SKU10"


def test_pricing_service_batch() -> None:
    svc = PricingService()
    contexts = [
        PricingContext(product_id="SKU1", our_price=150, competitor_info=CompetitorPriceInfo.from_dict({"s": 120})),
        PricingContext(product_id="SKU2", our_price=90, competitor_info=CompetitorPriceInfo.from_dict({"s": 120})),
    ]
    results = svc.evaluate_batch(contexts)
    assert len(results) == 2
    assert all(isinstance(r, tuple) and len(r) == 2 for r in results)


def test_pricing_service_stats() -> None:
    svc = PricingService()
    # قبل أي قرار
    stats = svc.stats
    assert stats["decisions_count"] == 0
    assert stats["cache_hits"] == 0
    # بعد قرار
    svc.evaluate("SKU1", 100, 120)
    stats = svc.stats
    assert stats["decisions_count"] == 1


def test_pricing_service_cache_hit() -> None:
    svc = PricingService()
    svc.evaluate("SKU1", 100, 120)
    svc.evaluate("SKU1", 100, 120)  # نفس المدخلات
    stats = svc.stats
    assert stats["cache_hits"] == 1
    assert stats["hit_rate"] == 0.5


def test_pricing_context_from_row() -> None:
    row = {"السعر": 150, "سعر_المنافس": 120, "المنافس": "store_a", "الماركة": "Dior", "المنتج": "Sauvage"}
    ctx = PricingService.build_context_from_row(row, "SKU1")
    assert ctx.our_price == 150
    assert ctx.competitor_info.min_price == 120


# ── اختبارات v3: التسعير الديناميكي ────────────────────────────────

from services.pricing_service import (
    DemandSignal,
    DynamicPricingContext,
    DynamicPricingEngine,
    MarketPosition,
    PricingServiceV3,
    competitive_index,
    price_sensitivity_analysis,
)


def test_estimate_elasticity() -> None:
    assert DynamicPricingEngine.estimate_elasticity(brand="Creed") == -0.8
    assert DynamicPricingEngine.estimate_elasticity(brand="Dior") == -1.5
    assert DynamicPricingEngine.estimate_elasticity(brand="", category="body_mist") == -2.2


def test_inventory_adjustment_scarcity() -> None:
    engine = DynamicPricingEngine()
    factor, reason = engine.inventory_adjustment(qty=5, sales_velocity=2.0)
    assert factor > 1.0
    assert "ندرة" in reason


def test_inventory_adjustment_clearance() -> None:
    engine = DynamicPricingEngine()
    factor, reason = engine.inventory_adjustment(qty=200, sales_velocity=0.1)
    assert factor < 1.0
    assert "تكدس" in reason or "بطء" in reason


def test_inventory_adjustment_normal() -> None:
    engine = DynamicPricingEngine()
    factor, reason = engine.inventory_adjustment(qty=50, sales_velocity=1.0)
    assert factor == 1.0
    assert reason == ""


def test_time_adjustment_peak() -> None:
    engine = DynamicPricingEngine()
    factor, reason = engine.time_adjustment(hour=21, weekday=4)
    assert factor > 1.0
    assert "ذروة" in reason or "نهاية" in reason


def test_time_adjustment_normal() -> None:
    engine = DynamicPricingEngine()
    factor, reason = engine.time_adjustment(hour=10, weekday=0)
    assert factor == 1.0
    assert reason == ""


def test_target_position_cheapest() -> None:
    engine = DynamicPricingEngine()
    comp = CompetitorPriceInfo.from_dict({"a": 100, "b": 120})
    price, reason = engine.target_position_price(comp, MarketPosition(target="cheapest", min_gap_pct=5), our_cost=50)
    assert price < 100
    assert "أرخص" in reason


def test_target_position_premium() -> None:
    engine = DynamicPricingEngine()
    comp = CompetitorPriceInfo.from_dict({"a": 100, "b": 120})
    price, reason = engine.target_position_price(comp, MarketPosition(target="premium", tolerance_pct=10), our_cost=50)
    assert price > 110
    assert "متميز" in reason


def test_optimize_revenue() -> None:
    engine = DynamicPricingEngine()
    dctx = DynamicPricingContext(
        base_ctx=PricingContext(
            our_price=150,
            cost_price=80,
            competitor_info=CompetitorPriceInfo.from_dict({"s": 120}),
        ),
        demand=DemandSignal(elasticity=-1.5, inventory_qty=50),
    )
    optimal, reason = engine.optimize_revenue(dctx)
    assert optimal > 0
    assert "أمثلية" in reason


def test_pricing_service_v3_dynamic() -> None:
    svc = PricingServiceV3()
    dctx = DynamicPricingContext(
        base_ctx=PricingContext(
            product_id="SKU_D1",
            our_price=150,
            cost_price=80,
            competitor_info=CompetitorPriceInfo.from_dict({"s": 120}),
        ),
        demand=DemandSignal(elasticity=-1.5, inventory_qty=50),
    )
    decision, diff = svc.evaluate_dynamic(dctx)
    assert decision.action is not None
    assert diff.product_id == "SKU_D1"


def test_price_sensitivity_analysis() -> None:
    result = price_sensitivity_analysis(
        our_price=150,
        competitor_prices={"a": 100, "b": 120, "c": 130},
        elasticity=-1.5,
    )
    assert "optimal_price" in result
    assert "recommendation" in result
    assert result["position"] in {"cheapest", "competitive", "expensive", "premium"}


def test_competitive_index() -> None:
    result = competitive_index(
        our_price=110,
        competitor_prices={"a": 100, "b": 120},
    )
    assert 0 <= result["index"] <= 100
    assert result["rank"] == 2
    assert result["total_competitors"] == 2


def test_competitive_index_cheapest() -> None:
    result = competitive_index(
        our_price=90,
        competitor_prices={"a": 100, "b": 120},
    )
    assert result["rank"] == 1
    assert result["index"] == 100


def test_pricing_service_v3_target_position() -> None:
    svc = PricingServiceV3()
    comp = CompetitorPriceInfo.from_dict({"a": 100, "b": 120})
    price, reason = svc.target_position(comp, MarketPosition(target="competitive"), our_cost=50)
    assert 100 <= price <= 120
    assert "تنافسي" in reason


def test_pricing_service_v3_optimize_batch() -> None:
    svc = PricingServiceV3()
    contexts = [
        DynamicPricingContext(
            base_ctx=PricingContext(product_id="B1", our_price=150, competitor_info=CompetitorPriceInfo.from_dict({"s": 120})),
            demand=DemandSignal(elasticity=-1.5),
        ),
        DynamicPricingContext(
            base_ctx=PricingContext(product_id="B2", our_price=90, competitor_info=CompetitorPriceInfo.from_dict({"s": 120})),
            demand=DemandSignal(elasticity=-2.0),
        ),
    ]
    results = svc.optimize_batch(contexts)
    assert len(results) == 2
    assert all(isinstance(r, tuple) and len(r) == 2 for r in results)
