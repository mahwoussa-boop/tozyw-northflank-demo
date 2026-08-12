"""core/product_entity.py — كيان المنتج الموحّد مع آلة حالات وتتبع أحداث.

يمثّل المنتج ككيان دائم عبر كل مراحل دورة حياته:
  اكتشاف → مطابقة → تصنيف → تسعير → مراجعة → موافقة → إرسال → متابعة

القواعد:
- ``entity_id`` ثابت عبر كل التحليلات (مشتق من product_id + source hash).
- ``state`` لا يتغير إلا عبر ``transition()`` (يفرض التحولات المسموحة).
- ``events`` سجل زمني كامل (append-only).
- ``price_history`` يتتبع كل تغيير سعري.

التوافق: يعمل **بجانب** ``core.models.Product`` — لا يستبدله. يوفّر
``from_product()`` و ``from_row()`` لبناء كيان من البيانات الموجودة.

⚠️ لا يستورد Streamlit أبداً. لا يستورد أي وحدة من engines/ أو utils/.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence


def _utcnow() -> datetime:
    """UTC الحالي (aware) — بديل ``datetime.utcnow()`` المُهمَل (3.12+)."""
    return datetime.now(timezone.utc)


# ════════════════════════════════════════════════════════════════════
#  حالات دورة الحياة
# ════════════════════════════════════════════════════════════════════

class ProductState(str, Enum):
    """حالات دورة حياة المنتج — 16 حالة واضحة."""

    DISCOVERED = "discovered"
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    CLASSIFIED = "classified"
    PRICED = "priced"
    AI_REVIEWED = "ai_reviewed"
    PENDING_APPROVAL = "pending_approval"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    SENT = "sent"
    TRACKED = "tracked"
    RE_EVALUATED = "re_evaluated"
    MISSING_CANDIDATE = "missing_candidate"
    ENRICHED = "enriched"


# ════════════════════════════════════════════════════════════════════
#  خريطة التحولات المسموحة
# ════════════════════════════════════════════════════════════════════

VALID_TRANSITIONS: dict[ProductState, frozenset[ProductState]] = {
    ProductState.DISCOVERED: frozenset({
        ProductState.MATCHED,
        ProductState.UNMATCHED,
    }),
    ProductState.MATCHED: frozenset({
        ProductState.CLASSIFIED,
    }),
    ProductState.UNMATCHED: frozenset({
        ProductState.MISSING_CANDIDATE,
    }),
    ProductState.CLASSIFIED: frozenset({
        ProductState.PRICED,
    }),
    ProductState.PRICED: frozenset({
        ProductState.AI_REVIEWED,
    }),
    ProductState.AI_REVIEWED: frozenset({
        ProductState.PENDING_APPROVAL,
        ProductState.AUTO_APPROVED,
    }),
    ProductState.PENDING_APPROVAL: frozenset({
        ProductState.APPROVED,
        ProductState.REJECTED,
        ProductState.MODIFIED,
    }),
    ProductState.AUTO_APPROVED: frozenset({
        ProductState.SENT,
    }),
    ProductState.APPROVED: frozenset({
        ProductState.SENT,
    }),
    ProductState.MODIFIED: frozenset({
        ProductState.SENT,
    }),
    ProductState.SENT: frozenset({
        ProductState.TRACKED,
    }),
    ProductState.TRACKED: frozenset({
        ProductState.RE_EVALUATED,
    }),
    ProductState.RE_EVALUATED: frozenset({
        ProductState.CLASSIFIED,
    }),
    ProductState.REJECTED: frozenset({
        ProductState.CLASSIFIED,
    }),
    ProductState.MISSING_CANDIDATE: frozenset({
        ProductState.ENRICHED,
    }),
    ProductState.ENRICHED: frozenset({
        ProductState.SENT,
    }),
}


# ════════════════════════════════════════════════════════════════════
#  أنواع الأحداث
# ════════════════════════════════════════════════════════════════════

class EventType(str, Enum):
    """أنواع الأحداث المسجّلة في سجل المنتج."""

    STATE_CHANGE = "state_change"
    PRICE_UPDATE = "price_update"
    USER_DECISION = "user_decision"
    MARKET_UPDATE = "market_update"
    AI_ANALYSIS = "ai_analysis"
    COMPETITOR_CHANGE = "competitor_change"


# ════════════════════════════════════════════════════════════════════
#  الحدث
# ════════════════════════════════════════════════════════════════════

@dataclass
class ProductEvent:
    """حدث واحد في تاريخ المنتج (Event Sourcing خفيف).

    كل حدث غير قابل للتعديل بعد الإنشاء (append-only). الأحداث تُخزَّن
    في جدول ``product_events`` عبر ``ProductRepository``.
    """

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=_utcnow)
    event_type: str = EventType.STATE_CHANGE
    old_state: Optional[str] = None
    new_state: Optional[str] = None
    actor: str = "system"
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """تحويل لقاموس للتخزين."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "actor": self.actor,
            "data": dict(self.data),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProductEvent":
        """بناء من قاموس مُخزَّن."""
        ts = d.get("timestamp", "")
        if isinstance(ts, str) and ts:
            try:
                parsed_ts = datetime.fromisoformat(ts)
            except ValueError:
                parsed_ts = _utcnow()
        elif isinstance(ts, datetime):
            parsed_ts = ts
        else:
            parsed_ts = _utcnow()
        return cls(
            event_id=d.get("event_id", uuid.uuid4().hex[:12]),
            timestamp=parsed_ts,
            event_type=d.get("event_type", EventType.STATE_CHANGE),
            old_state=d.get("old_state"),
            new_state=d.get("new_state"),
            actor=d.get("actor", "system"),
            data=dict(d.get("data") or {}),
            metadata=dict(d.get("metadata") or {}),
        )


# ════════════════════════════════════════════════════════════════════
#  استثناء التحول غير المسموح
# ════════════════════════════════════════════════════════════════════

class InvalidTransitionError(Exception):
    """يُرفع عند محاولة تحوّل غير مسموح في آلة الحالات."""

    def __init__(self, current: ProductState, target: ProductState) -> None:
        self.current = current
        self.target = target
        allowed = VALID_TRANSITIONS.get(current, frozenset())
        allowed_str = ", ".join(s.value for s in sorted(allowed, key=lambda s: s.value))
        super().__init__(
            f"تحوّل غير مسموح: {current.value} → {target.value}. "
            f"التحولات المسموحة من {current.value}: [{allowed_str}]"
        )


# ════════════════════════════════════════════════════════════════════
#  توليد entity_id ثابت
# ════════════════════════════════════════════════════════════════════

def make_entity_id(product_id: str, source: str = "") -> str:
    """يولّد entity_id ثابت ومُعاد إنتاجه من product_id + source.

    نفس (product_id, source) → نفس entity_id دائماً. هذا يضمن عدم
    تكرار الكيانات عند إعادة التحليل.

    إذا كان product_id فارغاً، يولّد UUID عشوائي (للمنتجات المفقودة
    التي ليس لها معرّف في كتالوجنا).
    """
    pid = str(product_id).strip()
    src = str(source).strip()
    if not pid:
        return uuid.uuid4().hex[:16]
    key = f"{pid}|{src}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════════════
#  كيان المنتج
# ════════════════════════════════════════════════════════════════════

@dataclass
class ProductEntity:
    """كيان المنتج الموحّد — يحمل كل تاريخه عبر دورة الحياة.

    يُستخدم كطبقة فوق ``core.models.Product`` القائم بلا استبدال. كل منتج
    يدخل ``portfolio_analyze()`` يحصل على كيان بـ ``entity_id`` ثابت.

    ضمانات:
    - ``transition()`` يفرض التحولات المسموحة (آلة حالات).
    - ``events`` سجل append-only لا يُحذف منه (Event Sourcing خفيف).
    - ``price_history`` يتتبع كل تغيير سعري بتوقيت ومصدر.
    - ``competitor_prices`` خريطة {store_name: price} لآخر أسعار المنافسين.
    """

    entity_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    product_id: str = ""
    name: str = ""
    brand: Optional[str] = None
    state: ProductState = ProductState.DISCOVERED
    current_price: float = 0.0
    suggested_price: Optional[float] = None
    competitor_prices: dict[str, float] = field(default_factory=dict)
    section: Optional[str] = None
    ai_confidence: float = 0.0
    events: list[ProductEvent] = field(default_factory=list)
    price_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    # ── آلة الحالات ─────────────────────────────────────────────

    def transition(
        self,
        target: ProductState,
        *,
        actor: str = "system",
        data: Optional[dict[str, Any]] = None,
    ) -> ProductEvent:
        """ينقل المنتج لحالة جديدة مع تسجيل الحدث.

        يرفع ``InvalidTransitionError`` عند تحوّل غير مسموح.
        يُعيد الحدث المُسجَّل.
        """
        allowed = VALID_TRANSITIONS.get(self.state, frozenset())
        if target not in allowed:
            raise InvalidTransitionError(self.state, target)

        event = ProductEvent(
            event_type=EventType.STATE_CHANGE,
            old_state=self.state.value,
            new_state=target.value,
            actor=actor,
            data=data or {},
        )
        self.events.append(event)
        self.state = target
        self.updated_at = event.timestamp
        return event

    # ── تسجيل تغيير سعري ───────────────────────────────────────

    def record_price_change(
        self,
        new_price: float,
        source: str,
        *,
        actor: str = "system",
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProductEvent:
        """يسجّل تغيير سعري كحدث في التاريخ.

        يُحدّث ``current_price`` ويضيف سجلاً في ``price_history``.
        """
        old_price = self.current_price
        event = ProductEvent(
            event_type=EventType.PRICE_UPDATE,
            actor=actor,
            data={
                "old_price": old_price,
                "new_price": new_price,
                "source": source,
                "delta_pct": round(
                    ((new_price - old_price) / old_price * 100) if old_price > 0 else 0.0,
                    2,
                ),
            },
            metadata=metadata or {},
        )
        self.price_history.append({
            "price": new_price,
            "source": source,
            "timestamp": event.timestamp.isoformat(),
            "old_price": old_price,
        })
        self.events.append(event)
        self.current_price = new_price
        self.updated_at = event.timestamp
        return event

    # ── تسجيل تغيير سعر منافس ──────────────────────────────────

    def record_competitor_price(
        self,
        store: str,
        price: float,
        *,
        actor: str = "system",
    ) -> ProductEvent:
        """يسجّل تغيير سعر منافس ويحدّث الخريطة."""
        old_price = self.competitor_prices.get(store)
        self.competitor_prices[store] = price
        event = ProductEvent(
            event_type=EventType.COMPETITOR_CHANGE,
            actor=actor,
            data={
                "store": store,
                "old_price": old_price,
                "new_price": price,
            },
        )
        self.events.append(event)
        self.updated_at = event.timestamp
        return event

    # ── تسجيل قرار مستخدم ──────────────────────────────────────

    def record_user_decision(
        self,
        action: str,
        *,
        user_price: float = 0.0,
        note: str = "",
    ) -> ProductEvent:
        """يسجّل قرار مستخدم (approve/reject/modify)."""
        event = ProductEvent(
            event_type=EventType.USER_DECISION,
            actor="user",
            data={
                "action": action,
                "user_price": user_price,
                "note": note,
                "ai_suggested": self.suggested_price,
                "ai_confidence": self.ai_confidence,
            },
        )
        self.events.append(event)
        self.updated_at = event.timestamp
        return event

    # ── تسجيل تحليل AI ────────────────────────────────────────

    def record_ai_analysis(
        self,
        suggested_price: float,
        confidence: float,
        reasoning: str = "",
        *,
        model: str = "",
        actor: str = "ai_analyst",
    ) -> ProductEvent:
        """يسجّل نتيجة تحليل AI ويحدّث السعر المقترح والثقة."""
        self.suggested_price = suggested_price
        self.ai_confidence = confidence
        event = ProductEvent(
            event_type=EventType.AI_ANALYSIS,
            actor=actor,
            data={
                "suggested_price": suggested_price,
                "confidence": confidence,
                "reasoning": reasoning,
                "model": model,
            },
        )
        self.events.append(event)
        self.updated_at = event.timestamp
        return event

    # ── خصائص مشتقة ──────────────────────────────────────────

    @property
    def timeline(self) -> list[dict[str, Any]]:
        """الخط الزمني الكامل مرتباً زمنياً."""
        return [
            e.to_dict()
            for e in sorted(self.events, key=lambda e: e.timestamp)
        ]

    @property
    def days_since_last_update(self) -> float:
        """عدد الأيام منذ آخر تحديث."""
        now = _utcnow()
        updated = self.updated_at
        # ضمان توافق naive/aware: أي updated_at بدون tzinfo يُفترض UTC
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        delta = now - updated
        return delta.total_seconds() / 86400.0

    @property
    def event_count(self) -> int:
        """عدد الأحداث المسجّلة."""
        return len(self.events)

    @property
    def is_actionable(self) -> bool:
        """هل المنتج في حالة تتطلب إجراءً؟"""
        return self.state in {
            ProductState.PENDING_APPROVAL,
            ProductState.AI_REVIEWED,
            ProductState.PRICED,
        }

    @property
    def is_terminal(self) -> bool:
        """هل المنتج في حالة نهائية (مُرسل أو مرفوض)؟"""
        return self.state in {
            ProductState.SENT,
            ProductState.TRACKED,
        }

    @property
    def min_competitor_price(self) -> Optional[float]:
        """أقل سعر منافس حالي."""
        valid = [p for p in self.competitor_prices.values() if p > 0]
        return min(valid) if valid else None

    # ── المصانع ──────────────────────────────────────────────

    @classmethod
    def from_product(
        cls,
        product_id: str,
        name: str,
        price: float,
        *,
        brand: Optional[str] = None,
        source: str = "",
    ) -> "ProductEntity":
        """ينشئ كياناً من بيانات منتج أساسية.

        ``entity_id`` مشتق من ``product_id`` + ``source`` (ثابت عبر التحليلات).
        """
        eid = make_entity_id(product_id, source)
        return cls(
            entity_id=eid,
            product_id=str(product_id).strip(),
            name=str(name).strip(),
            brand=brand,
            current_price=float(price) if price else 0.0,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any], *, source: str = "") -> "ProductEntity":
        """ينشئ كياناً من صف DataFrame (أعمدة عربية).

        يدعم الأسماء العربية: معرف_المنتج، المنتج، السعر، الماركة.
        """
        pid = str(row.get("معرف_المنتج", row.get("product_id", ""))).strip()
        name = str(row.get("المنتج", row.get("name", ""))).strip()
        price = row.get("السعر", row.get("price", 0.0))
        brand = row.get("الماركة", row.get("brand"))

        try:
            price_f = float(price) if price else 0.0
        except (TypeError, ValueError):
            price_f = 0.0

        return cls.from_product(
            product_id=pid,
            name=name,
            price=price_f,
            brand=str(brand) if brand else None,
            source=source,
        )

    # ── التسلسل ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """تحويل الكيان لقاموس للتخزين في DB."""
        return {
            "entity_id": self.entity_id,
            "product_id": self.product_id,
            "name": self.name,
            "brand": self.brand,
            "state": self.state.value,
            "current_price": self.current_price,
            "suggested_price": self.suggested_price,
            "competitor_prices": dict(self.competitor_prices),
            "section": self.section,
            "ai_confidence": self.ai_confidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProductEntity":
        """بناء كيان من قاموس مُخزَّن."""
        state_str = d.get("state", "discovered")
        try:
            state = ProductState(state_str)
        except ValueError:
            state = ProductState.DISCOVERED

        created = d.get("created_at", "")
        updated = d.get("updated_at", "")

        def _parse_dt(val: Any) -> datetime:
            if isinstance(val, datetime):
                return val
            if isinstance(val, str) and val:
                try:
                    return datetime.fromisoformat(val)
                except ValueError:
                    pass
            return _utcnow()

        return cls(
            entity_id=d.get("entity_id", uuid.uuid4().hex[:16]),
            product_id=d.get("product_id", ""),
            name=d.get("name", ""),
            brand=d.get("brand"),
            state=state,
            current_price=float(d.get("current_price", 0.0)),
            suggested_price=d.get("suggested_price"),
            competitor_prices=dict(d.get("competitor_prices") or {}),
            section=d.get("section"),
            ai_confidence=float(d.get("ai_confidence", 0.0)),
            created_at=_parse_dt(created),
            updated_at=_parse_dt(updated),
        )
