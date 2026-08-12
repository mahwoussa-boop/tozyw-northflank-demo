"""services/workflow/smart_batcher.py — تجميع ذكي لعناصر التسعير في دُفعات.

يقدّم تجميعاً ذكياً لعناصر التسعير حسب العلامة التجارية أو الأولوية أو القسم،
مع ترتيب أمثل لإرسال الدُّفعات:
  - تجميع حسب العلامة التجارية مع ترتيب تنازلي بالتأثير السعري.
  - تجميع حسب الأولوية (عاجل / عادي / منخفض).
  - تجميع حسب القسم (رفع سعر / خفض سعر / ...).
  - تقسيم المجموعات إلى دُفعات بحجم أقصى قابل للضبط.

⚠️ لا يستورد streamlit — خدمة خالصة بلا حالة (stateless).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────
# نموذج البيانات
# ──────────────────────────────────────────────────────────────

@dataclass
class BatchGroup:
    """مجموعة دُفعة واحدة تحتوي على عناصر متشابهة.

    Attributes:
        group_id: معرّف فريد مُختصر (12 محرف hex).
        group_key: مفتاح التجميع (اسم العلامة أو مستوى الأولوية أو اسم القسم).
        group_type: نوع التجميع (brand | priority | section).
        items: قائمة العناصر المنتمية للمجموعة.
        total_price_delta: إجمالي فرق السعر (التأثير الكلي).
        avg_confidence: متوسط مستوى الثقة لعناصر المجموعة.
    """

    group_id: str
    group_key: str
    group_type: str
    items: list[dict] = field(default_factory=list)
    total_price_delta: float = 0.0
    avg_confidence: float = 0.0


# ──────────────────────────────────────────────────────────────
# ثوابت الأولوية
# ──────────────────────────────────────────────────────────────

_PRIORITY_ORDER = {"urgent": 0, "normal": 1, "low": 2}


# ──────────────────────────────────────────────────────────────
# الخدمة الرئيسية
# ──────────────────────────────────────────────────────────────

class SmartBatcher:
    """مُجمِّع ذكي — يصنّف العناصر في مجموعات ويرتّبها للإرسال الأمثل.

    خدمة بلا حالة (stateless): لا تحتاج قاعدة بيانات ولا تعتمد
    على أيّ حالة عالمية. كل استدعاء مستقلّ تماماً.
    """

    # ── أدوات داخلية ──────────────────────────────────────────

    @staticmethod
    def _make_group_id() -> str:
        """يُولّد معرّف مجموعة فريد مُختصر (12 محرف hex)."""
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _calc_delta(item: dict) -> float:
        """يحسب فرق السعر لعنصر واحد.

        يستخدم الحقل ``price_delta`` إن وُجد، وإلّا يحسبه من
        ``suggested_price`` و ``current_price``.
        """
        if "price_delta" in item:
            return float(item["price_delta"])
        suggested = float(item.get("suggested_price", 0))
        current = float(item.get("current_price", 0))
        return abs(suggested - current)

    @staticmethod
    def _calc_confidence(item: dict) -> float:
        """يستخرج مستوى الثقة من العنصر (افتراضي 0.0)."""
        return float(item.get("confidence", 0.0))

    def _build_group(
        self,
        key: str,
        group_type: str,
        items: list[dict],
    ) -> BatchGroup:
        """يبني كائن BatchGroup من قائمة عناصر.

        Args:
            key: مفتاح التجميع.
            group_type: نوع التجميع.
            items: العناصر المنتمية للمجموعة.

        Returns:
            كائن ``BatchGroup`` مع حساب إجمالي الفرق ومتوسط الثقة.
        """
        total_delta = sum(self._calc_delta(it) for it in items)
        confidences = [self._calc_confidence(it) for it in items]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return BatchGroup(
            group_id=self._make_group_id(),
            group_key=key,
            group_type=group_type,
            items=list(items),
            total_price_delta=total_delta,
            avg_confidence=avg_conf,
        )

    # ── التجميع حسب العلامة التجارية ──────────────────────────

    def group_by_brand(self, items: list[dict]) -> list[BatchGroup]:
        """يجمّع العناصر حسب العلامة التجارية ويرتّبها تنازلياً بالتأثير السعري.

        Args:
            items: قائمة عناصر التسعير، كل عنصر يحتوي مفتاح ``brand``.

        Returns:
            قائمة ``BatchGroup`` مرتّبة تنازلياً حسب ``total_price_delta``.
        """
        buckets: dict[str, list[dict]] = {}
        for item in items:
            brand = item.get("brand", "unknown")
            buckets.setdefault(brand, []).append(item)

        groups = [
            self._build_group(brand, "brand", bucket_items)
            for brand, bucket_items in buckets.items()
        ]
        groups.sort(key=lambda g: g.total_price_delta, reverse=True)
        return groups

    # ── التجميع حسب الأولوية ──────────────────────────────────

    def group_by_priority(self, items: list[dict]) -> list[BatchGroup]:
        """يجمّع العناصر إلى 3 مستويات أولوية: عاجل / عادي / منخفض.

        - عاجل (urgent): الثقة >= 0.9
        - عادي (normal): الثقة بين 0.7 و 0.9 (حصرياً من الأعلى)
        - منخفض (low): الثقة < 0.7

        Args:
            items: قائمة عناصر التسعير مع حقل ``confidence``.

        Returns:
            قائمة ``BatchGroup`` مرتّبة: عاجل أولاً ثم عادي ثم منخفض.
            المجموعات الفارغة لا تُضاف.
        """
        buckets: dict[str, list[dict]] = {
            "urgent": [],
            "normal": [],
            "low": [],
        }

        for item in items:
            conf = self._calc_confidence(item)
            if conf >= 0.9:
                buckets["urgent"].append(item)
            elif conf >= 0.7:
                buckets["normal"].append(item)
            else:
                buckets["low"].append(item)

        groups: list[BatchGroup] = []
        for level in ("urgent", "normal", "low"):
            if buckets[level]:
                groups.append(
                    self._build_group(level, "priority", buckets[level])
                )
        return groups

    # ── التجميع حسب القسم ─────────────────────────────────────

    def group_by_section(self, items: list[dict]) -> list[BatchGroup]:
        """يجمّع العناصر حسب القسم (مثل price_raise، price_lower، ...).

        Args:
            items: قائمة عناصر التسعير مع حقل ``section``.

        Returns:
            قائمة ``BatchGroup`` مجمّعة حسب القسم.
        """
        buckets: dict[str, list[dict]] = {}
        for item in items:
            section = item.get("section", "unknown")
            buckets.setdefault(section, []).append(item)

        return [
            self._build_group(section, "section", bucket_items)
            for section, bucket_items in buckets.items()
        ]

    # ── ترتيب وتقسيم الدُّفعات ────────────────────────────────

    def optimize_batch_order(
        self,
        groups: list[BatchGroup],
        *,
        max_per_batch: int = 50,
    ) -> list[list[dict]]:
        """يُسطّح المجموعات ويقسمها إلى دُفعات بحجم أقصى.

        يأخذ المجموعات بترتيبها الحالي (الذي يفترض أنه مُحسَّن مسبقاً)
        ويفرد عناصرها ثم يقسمها إلى دُفعات لا تتجاوز ``max_per_batch``.

        Args:
            groups: قائمة ``BatchGroup`` مرتّبة.
            max_per_batch: الحدّ الأقصى لعدد العناصر في الدُّفعة الواحدة.

        Returns:
            قائمة من الدُّفعات، كل دُفعة عبارة عن قائمة قواميس جاهزة للإرسال.
        """
        if not groups:
            return []

        # تسطيح جميع العناصر بترتيب المجموعات
        flat: list[dict] = []
        for group in groups:
            flat.extend(group.items)

        if not flat:
            return []

        # تقسيم إلى دُفعات
        batches: list[list[dict]] = []
        for i in range(0, len(flat), max_per_batch):
            batches.append(flat[i : i + max_per_batch])
        return batches
