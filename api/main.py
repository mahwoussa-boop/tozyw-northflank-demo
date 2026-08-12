"""api/main.py — تطبيق FastAPI الرئيسي لنظام محسوس التسعير v4.

يوفّر طبقة API تفصل محرك التسعير عن واجهة Streamlit:
- نقاط وصول للمنتجات والقرارات والتنبيهات.
- تحليل السَّرب (Swarm) والذكاء التسعيري.
- الطيار الآلي والمراقبة.

⚠️ لا يستورد Streamlit أبداً.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.deps import get_container

logger = logging.getLogger("api")

# ════════════════════════════════════════════════════════════════════
#  تطبيق FastAPI
# ════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Mahwous Pricing API",
    description="واجهة برمجية لنظام محسوس التسعير v4",
    version="4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://localhost:8502", "http://127.0.0.1:8501", "http://127.0.0.1:8502"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════════
#  نماذج الطلبات والاستجابات (Pydantic)
# ════════════════════════════════════════════════════════════════════

class SwarmAnalyzeRequest(BaseModel):
    """طلب تحليل السَّرب لمنتج واحد."""
    product_name: str = Field(..., description="اسم المنتج")
    brand: str = Field("", description="العلامة التجارية")
    our_price: float = Field(..., description="سعرنا الحالي")
    competitor_prices: list[float] = Field(..., description="أسعار المنافسين")
    section: str = Field("", description="القسم")


class DecisionPriceRequest(BaseModel):
    """طلب موافقة أو تعديل قرار بسعر."""
    price: float = Field(..., description="السعر المُعتمَد")


class AutoPilotToggleRequest(BaseModel):
    """طلب تبديل حالة الطيار الآلي."""
    enabled: bool = Field(..., description="تفعيل / تعطيل")


class AutoPilotEvaluateRequest(BaseModel):
    """طلب تقييم منتج عبر الطيار الآلي."""
    product_name: str = Field(..., description="اسم المنتج")
    brand: str = Field("", description="العلامة التجارية")
    current_price: float = Field(..., description="السعر الحالي")
    suggested_price: float = Field(..., description="السعر المقترح")
    confidence: float = Field(..., description="مستوى الثقة (0-1)")
    consensus_type: str = Field("unanimous", description="نوع الإجماع")


class PredictRequest(BaseModel):
    """طلب تنبؤ باتجاه السعر."""
    prices: list[float] = Field(..., description="تاريخ الأسعار مرتب زمنياً")
    product_name: str = Field("", description="اسم المنتج")


# ════════════════════════════════════════════════════════════════════
#  Health
# ════════════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/health",
    summary="فحص صحة النظام",
    description="يُعيد حالة النظام والإصدار وعدد الاختبارات.",
)
async def health_check() -> dict[str, Any]:
    """نقطة فحص صحة النظام."""
    return {"status": "ok", "version": "4.0", "tests_count": 585}


# ════════════════════════════════════════════════════════════════════
#  Products
# ════════════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/products",
    summary="قائمة المنتجات حسب الحالة",
    description="يسترجع قائمة المنتجات مع ترقيم الصفحات.",
)
async def list_products(
    state: Optional[str] = Query(None, description="حالة المنتج (مثلاً: discovered)"),
    skip: int = Query(0, ge=0, description="عدد العناصر المُتخطّاة"),
    limit: int = Query(100, ge=1, le=500, description="الحد الأقصى للنتائج"),
) -> dict[str, Any]:
    """يسترجع المنتجات حسب الحالة مع ترقيم صفحات."""
    c = get_container()
    try:
        if state:
            from core.product_entity import ProductState
            try:
                ps = ProductState(state)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"حالة غير صالحة: {state}",
                )
            entities = c.product_repo.get_by_state(ps, limit=skip + limit)
            page = entities[skip: skip + limit]
        else:
            # استرجاع عام — حسب الحالات المتاحة
            rows = c.product_repo._db.query(
                "SELECT * FROM product_entities ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, skip),
            )
            page = [c.product_repo._row_to_entity(r) for r in rows]

        return {
            "products": [e.to_dict() for e in page],
            "skip": skip,
            "limit": limit,
            "count": len(page),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("خطأ في قائمة المنتجات: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/api/v1/products/stats",
    summary="إحصائيات المنتجات حسب الحالة",
    description="يُعيد عدد المنتجات لكل حالة.",
)
async def products_stats() -> dict[str, Any]:
    """إحصائيات عدد المنتجات لكل حالة."""
    c = get_container()
    try:
        return c.product_repo.count_by_state()
    except Exception as exc:
        logger.error("خطأ في إحصائيات المنتجات: %s", exc)
        return {}


@app.get(
    "/api/v1/products/{entity_id}",
    summary="استرجاع منتج بمعرّفه",
    description="يسترجع كيان المنتج مع جميع أحداثه.",
)
async def get_product(entity_id: str) -> dict[str, Any]:
    """يسترجع كيان منتج مع أحداثه."""
    c = get_container()
    entity = c.product_repo.get(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"المنتج {entity_id} غير موجود")
    result = entity.to_dict()
    result["events"] = [e.to_dict() for e in entity.events]
    return result


@app.get(
    "/api/v1/products/{entity_id}/timeline",
    summary="الخط الزمني لمنتج",
    description="يسترجع جميع أحداث منتج مرتّبة زمنياً.",
)
async def product_timeline(entity_id: str) -> list[dict[str, Any]]:
    """الخط الزمني لكيان منتج."""
    c = get_container()
    entity = c.product_repo.get(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"المنتج {entity_id} غير موجود")
    return entity.timeline


# ════════════════════════════════════════════════════════════════════
#  Decisions
# ════════════════════════════════════════════════════════════════════

@app.post(
    "/api/v1/decisions/{decision_id}/approve",
    summary="الموافقة على قرار تسعير",
    description="يوافق على قرار تسعير بسعر محدد.",
)
async def approve_decision(
    decision_id: str, body: DecisionPriceRequest
) -> dict[str, Any]:
    """يوافق على قرار تسعير ويسجّل الحدث."""
    c = get_container()
    entity = c.product_repo.get(decision_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"القرار {decision_id} غير موجود")
    entity.record_user_decision("approve", user_price=body.price)
    c.product_repo.upsert(entity)
    # نسجّل الحدث الجديد فقط (الأخير): إعادة تسجيل الأحداث القديمة المُحمّلة من
    # القاعدة تخالف قيد UNIQUE على event_id (والأحداث append-only أصلاً).
    if entity.events:
        c.product_repo.record_event(entity.entity_id, entity.events[-1])
    return {"status": "approved", "entity_id": decision_id, "price": body.price}


@app.post(
    "/api/v1/decisions/{decision_id}/reject",
    summary="رفض قرار تسعير",
    description="يرفض قرار تسعير.",
)
async def reject_decision(decision_id: str) -> dict[str, Any]:
    """يرفض قرار تسعير ويسجّل الحدث."""
    c = get_container()
    entity = c.product_repo.get(decision_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"القرار {decision_id} غير موجود")
    entity.record_user_decision("reject")
    c.product_repo.upsert(entity)
    # نسجّل الحدث الجديد فقط (الأخير): إعادة تسجيل الأحداث القديمة المُحمّلة من
    # القاعدة تخالف قيد UNIQUE على event_id (والأحداث append-only أصلاً).
    if entity.events:
        c.product_repo.record_event(entity.entity_id, entity.events[-1])
    return {"status": "rejected", "entity_id": decision_id}


@app.post(
    "/api/v1/decisions/{decision_id}/modify",
    summary="تعديل قرار تسعير",
    description="يعدّل قرار تسعير بسعر جديد.",
)
async def modify_decision(
    decision_id: str, body: DecisionPriceRequest
) -> dict[str, Any]:
    """يعدّل قرار تسعير بسعر جديد."""
    c = get_container()
    entity = c.product_repo.get(decision_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"القرار {decision_id} غير موجود")
    entity.record_user_decision("modify", user_price=body.price)
    c.product_repo.upsert(entity)
    # نسجّل الحدث الجديد فقط (الأخير): إعادة تسجيل الأحداث القديمة المُحمّلة من
    # القاعدة تخالف قيد UNIQUE على event_id (والأحداث append-only أصلاً).
    if entity.events:
        c.product_repo.record_event(entity.entity_id, entity.events[-1])
    return {"status": "modified", "entity_id": decision_id, "price": body.price}


# ════════════════════════════════════════════════════════════════════
#  Swarm
# ════════════════════════════════════════════════════════════════════

@app.post(
    "/api/v1/swarm/analyze",
    summary="تحليل السَّرب لمنتج",
    description="يشغّل تحليل الذكاء الجماعي (Swarm) على منتج واحد.",
)
async def swarm_analyze(body: SwarmAnalyzeRequest) -> dict[str, Any]:
    """يشغّل تحليل السَّرب ويُعيد القرار."""
    c = get_container()
    try:
        decision = c.swarm.analyze(
            product_name=body.product_name,
            brand=body.brand,
            our_price=body.our_price,
            competitor_prices=body.competitor_prices,
            section=body.section,
        )
        return decision.to_dict()
    except Exception as exc:
        logger.error("خطأ في تحليل السَّرب: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ════════════════════════════════════════════════════════════════════
#  Notifications
# ════════════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/notifications",
    summary="استرجاع التنبيهات",
    description="يسترجع التنبيهات مع خيار فلترة غير المقروءة.",
)
async def get_notifications(
    unread_only: bool = Query(True, description="فقط غير المقروءة"),
    limit: int = Query(20, ge=1, le=100, description="الحد الأقصى"),
) -> list[dict[str, Any]]:
    """يسترجع التنبيهات."""
    c = get_container()
    if unread_only:
        notifs = c.notifier.get_unread(limit=limit)
    else:
        notifs = c.notifier.get_all(limit=limit)
    return [n.to_dict() for n in notifs]


@app.post(
    "/api/v1/notifications/{notif_id}/read",
    summary="تعليم تنبيه كمقروء",
    description="يعلّم تنبيهاً محدداً كمقروء.",
)
async def mark_notification_read(notif_id: str) -> dict[str, Any]:
    """يعلّم تنبيهاً كمقروء."""
    c = get_container()
    success = c.notifier.mark_read(notif_id)
    return {"notif_id": notif_id, "marked": success}


@app.get(
    "/api/v1/notifications/count",
    summary="عدد التنبيهات غير المقروءة",
    description="يُعيد عدد التنبيهات غير المقروءة.",
)
async def notifications_count() -> dict[str, int]:
    """عدد التنبيهات غير المقروءة."""
    c = get_container()
    return {"unread": c.notifier.unread_count()}


# ════════════════════════════════════════════════════════════════════
#  AutoPilot
# ════════════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/autopilot/status",
    summary="حالة الطيار الآلي",
    description="يُعيد إعدادات الطيار الآلي والإحصائيات اليومية.",
)
async def autopilot_status() -> dict[str, Any]:
    """إعدادات الطيار الآلي + إحصائيات اليوم."""
    c = get_container()
    config = c.autopilot._config
    daily = c.autopilot.get_daily_stats()
    return {
        "config": {
            "enabled": config.enabled,
            "min_confidence": config.min_confidence,
            "max_price_change_pct": config.max_price_change_pct,
            "excluded_brands": config.excluded_brands,
            "max_daily_actions": config.max_daily_actions,
            "require_consensus": config.require_consensus,
        },
        "daily_stats": daily,
    }


@app.post(
    "/api/v1/autopilot/toggle",
    summary="تبديل حالة الطيار الآلي",
    description="يفعّل أو يعطّل الطيار الآلي.",
)
async def autopilot_toggle(body: AutoPilotToggleRequest) -> dict[str, Any]:
    """يبدّل حالة الطيار الآلي (تفعيل / تعطيل)."""
    c = get_container()
    c.autopilot.update_config(enabled=body.enabled)
    return {"enabled": body.enabled, "status": "updated"}


@app.post(
    "/api/v1/autopilot/evaluate",
    summary="تقييم منتج عبر الطيار الآلي",
    description="يُقيّم قرار تسعير وفق قواعد الأمان.",
)
async def autopilot_evaluate(body: AutoPilotEvaluateRequest) -> dict[str, Any]:
    """يُقيّم قرار تسعير عبر الطيار الآلي."""
    c = get_container()
    verdict = c.autopilot.evaluate(
        product_name=body.product_name,
        brand=body.brand,
        current_price=body.current_price,
        suggested_price=body.suggested_price,
        confidence=body.confidence,
        consensus_type=body.consensus_type,
    )
    return {
        "can_execute": verdict.can_execute,
        "reason": verdict.reason,
        "approval_level": verdict.approval_level,
        "blocked_by": verdict.blocked_by,
    }


# ════════════════════════════════════════════════════════════════════
#  Intelligence
# ════════════════════════════════════════════════════════════════════

@app.post(
    "/api/v1/intelligence/predict",
    summary="تنبؤ باتجاه السعر",
    description="يحلل تاريخ الأسعار ويتنبأ بالاتجاه.",
)
async def predict_price_trend(body: PredictRequest) -> dict[str, Any]:
    """يتنبأ باتجاه السعر باستخدام انحدار خطي."""
    c = get_container()
    trend = c.predictor.predict(body.prices, product_name=body.product_name)
    return trend.to_dict()


@app.get(
    "/api/v1/intelligence/seasons",
    summary="المواسم القادمة",
    description="يُعيد المواسم التجارية القادمة خلال 90 يوماً.",
)
async def upcoming_seasons() -> list[dict[str, Any]]:
    """المواسم التجارية القادمة."""
    c = get_container()
    seasons = c.seasonal.get_upcoming_seasons()
    return [
        {
            "name": s.name,
            "active": s.active,
            "days_until": s.days_until,
            "expected_impact_pct": s.expected_impact_pct,
            "affected_categories": s.affected_categories,
            "description": s.description,
        }
        for s in seasons
    ]


@app.get(
    "/api/v1/intelligence/anomalies",
    summary="الشذوذ السعري الأخير",
    description="يسترجع الشذوذ السعري المكتشف مؤخراً.",
)
async def recent_anomalies(
    limit: int = Query(50, ge=1, le=200, description="الحد الأقصى"),
    severity: Optional[str] = Query(None, description="مستوى الشدة (info|warning|critical)"),
) -> list[dict[str, Any]]:
    """يسترجع الشذوذ السعري الأخير."""
    c = get_container()
    if severity:
        anomalies = c.anomaly.get_by_severity(severity, limit=limit)
    else:
        anomalies = c.anomaly.get_recent(limit=limit)
    return [a.to_dict() for a in anomalies]


# ════════════════════════════════════════════════════════════════════
#  Monitoring / Metrics
# ════════════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/metrics/dashboard",
    summary="لوحة مقاييس AI",
    description="يُعيد بيانات لوحة مقاييس الذكاء الاصطناعي.",
)
async def metrics_dashboard() -> dict[str, Any]:
    """بيانات لوحة مقاييس AI."""
    c = get_container()
    try:
        product_stats = c.product_repo.count_by_state()
    except Exception:
        product_stats = {}
    try:
        daily = c.autopilot.get_daily_stats()
    except Exception:
        daily = {}
    try:
        unread = c.notifier.unread_count()
    except Exception:
        unread = 0
    return {
        "product_stats": product_stats,
        "autopilot_daily": daily,
        "unread_notifications": unread,
    }
