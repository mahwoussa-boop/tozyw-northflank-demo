"""services/monitoring/price_monitor.py — مراقب الأسعار المستمر.

يراقب الأسعار باستمرار ويولّد تنبيهات عند اكتشاف تغييرات جوهرية في السوق.
يشمل: لقطات الأسعار، تحليل الموقع السعري، كشف التغيرات، والتقارير الدورية.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.product_entity import _utcnow
from infrastructure.db_manager import DatabaseManager


@dataclass
class PriceSnapshot:
    """لقطة سعرية لمنتج واحد تشمل سعرنا وأسعار المنافسين."""

    snapshot_id: str
    product_name: str
    brand: str
    our_price: float
    competitor_prices: dict[str, float] = field(default_factory=dict)
    avg_competitor_price: float = 0.0
    min_competitor_price: float = 0.0
    max_competitor_price: float = 0.0
    price_position: str = "competitive"
    gap_pct: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        """يحوّل اللقطة إلى قاموس قابل للتسلسل."""
        return {
            "snapshot_id": self.snapshot_id,
            "product_name": self.product_name,
            "brand": self.brand,
            "our_price": self.our_price,
            "competitor_prices": self.competitor_prices,
            "avg_competitor_price": self.avg_competitor_price,
            "min_competitor_price": self.min_competitor_price,
            "max_competitor_price": self.max_competitor_price,
            "price_position": self.price_position,
            "gap_pct": self.gap_pct,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PriceAlert:
    """تنبيه سعري يُنشأ عند اكتشاف تغيّر جوهري في السوق."""

    alert_id: str
    product_name: str
    alert_type: str  # competitor_undercut | price_war | opportunity | gap_widening
    severity: str  # info | warning | critical
    message: str
    old_value: float = 0.0
    new_value: float = 0.0
    change_pct: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class MonitoringReport:
    """تقرير مراقبة شامل يلخّص حالة الأسعار والتنبيهات."""

    report_id: str
    total_products: int = 0
    products_monitored: int = 0
    alerts_generated: int = 0
    cheapest_count: int = 0
    competitive_count: int = 0
    expensive_count: int = 0
    avg_gap_pct: float = 0.0
    snapshots: list[PriceSnapshot] = field(default_factory=list)
    alerts: list[PriceAlert] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)


class ContinuousPriceMonitor:
    """يراقب الأسعار باستمرار ويولّد تنبيهات عند اكتشاف تغييرات جوهرية.

    يعتمد على ``DatabaseManager`` لتخزين اللقطات والتنبيهات في SQLite.
    """

    def __init__(self, db: DatabaseManager) -> None:
        """يهيّئ المراقب مع مدير قاعدة البيانات.

        Args:
            db: مدير قاعدة البيانات للوصول إلى SQLite.
        """
        self._db = db

    # ────────────────── إعداد الجداول ──────────────────

    def ensure_table(self) -> None:
        """ينشئ جدولَي اللقطات والتنبيهات إن لم يكونا موجودَين."""
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS price_snapshots (
                snapshot_id         TEXT PRIMARY KEY,
                product_name        TEXT NOT NULL,
                brand               TEXT DEFAULT '',
                our_price           REAL,
                competitor_prices   TEXT DEFAULT '{}',
                avg_competitor_price REAL,
                min_competitor_price REAL,
                max_competitor_price REAL,
                price_position      TEXT,
                gap_pct             REAL,
                created_at          TEXT NOT NULL
            )
        """)
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_snap_product "
            "ON price_snapshots(product_name)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_snap_created "
            "ON price_snapshots(created_at)"
        )

        self._db.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id     TEXT PRIMARY KEY,
                product_name TEXT NOT NULL,
                alert_type   TEXT NOT NULL,
                severity     TEXT DEFAULT 'info',
                message      TEXT DEFAULT '',
                old_value    REAL DEFAULT 0,
                new_value    REAL DEFAULT 0,
                change_pct   REAL DEFAULT 0,
                created_at   TEXT NOT NULL
            )
        """)
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_product "
            "ON price_alerts(product_name)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_created "
            "ON price_alerts(created_at)"
        )

    # ────────────────── اللقطات السعرية ──────────────────

    def take_snapshot(
        self,
        *,
        product_name: str,
        brand: str = "",
        our_price: float,
        competitor_prices: dict[str, float],
    ) -> PriceSnapshot:
        """يأخذ لقطة سعرية لمنتج ويحسب موقعه في السوق.

        Args:
            product_name: اسم المنتج.
            brand: العلامة التجارية.
            our_price: سعرنا الحالي.
            competitor_prices: قاموس {اسم_المتجر: السعر}.

        Returns:
            لقطة سعرية كاملة مع تحليل الموقع السعري.
        """
        comp_values = [p for p in competitor_prices.values() if p > 0]

        if comp_values:
            avg_comp = sum(comp_values) / len(comp_values)
            min_comp = min(comp_values)
            max_comp = max(comp_values)
        else:
            avg_comp = 0.0
            min_comp = 0.0
            max_comp = 0.0

        position = self._classify_position(our_price, avg_comp, min_comp, max_comp)
        gap_pct = ((our_price - avg_comp) / avg_comp * 100) if avg_comp > 0 else 0.0

        snap = PriceSnapshot(
            snapshot_id=uuid.uuid4().hex[:12],
            product_name=product_name,
            brand=brand,
            our_price=our_price,
            competitor_prices=competitor_prices,
            avg_competitor_price=round(avg_comp, 2),
            min_competitor_price=round(min_comp, 2),
            max_competitor_price=round(max_comp, 2),
            price_position=position,
            gap_pct=round(gap_pct, 2),
            created_at=_utcnow(),
        )
        self._store_snapshot(snap)
        return snap

    def take_batch_snapshot(self, products: list[dict]) -> list[PriceSnapshot]:
        """يأخذ لقطات لمجموعة منتجات دفعة واحدة.

        Args:
            products: قائمة قواميس كل منها يحتوي
                      {product_name, brand, our_price, competitor_prices}.

        Returns:
            قائمة لقطات سعرية.
        """
        snapshots: list[PriceSnapshot] = []
        for prod in products:
            snap = self.take_snapshot(
                product_name=prod["product_name"],
                brand=prod.get("brand", ""),
                our_price=prod["our_price"],
                competitor_prices=prod["competitor_prices"],
            )
            snapshots.append(snap)
        return snapshots

    # ────────────────── كشف التغيرات ──────────────────

    def detect_changes(
        self, product_name: str, *, lookback_hours: int = 24
    ) -> list[PriceAlert]:
        """يكتشف التغيرات السعرية الجوهرية بين آخر لقطتين.

        Args:
            product_name: اسم المنتج للمقارنة.
            lookback_hours: ساعات الرجوع للبحث عن لقطات سابقة.

        Returns:
            قائمة تنبيهات بالتغيرات المكتشفة.
        """
        cutoff = (_utcnow() - timedelta(hours=lookback_hours)).isoformat()
        rows = self._db.query(
            "SELECT * FROM price_snapshots "
            "WHERE product_name = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 2",
            (product_name, cutoff),
        )

        if len(rows) < 2:
            return []

        current = self._row_to_snapshot(rows[0])
        previous = self._row_to_snapshot(rows[1])

        alerts: list[PriceAlert] = []
        now = _utcnow()

        # --- competitor_undercut: منافس خفّض سعره تحت سعرنا ---
        for store, new_price in current.competitor_prices.items():
            old_price = previous.competitor_prices.get(store)
            if old_price is not None and old_price >= current.our_price and new_price < current.our_price:
                chg = ((new_price - old_price) / old_price * 100) if old_price > 0 else 0
                alert = PriceAlert(
                    alert_id=uuid.uuid4().hex[:12],
                    product_name=product_name,
                    alert_type="competitor_undercut",
                    severity="warning",
                    message=f"⚠️ المتجر {store} خفّض سعره إلى {new_price:.2f} (كان {old_price:.2f}) وأصبح أقل من سعرنا {current.our_price:.2f}",
                    old_value=old_price,
                    new_value=new_price,
                    change_pct=round(chg, 2),
                    created_at=now,
                )
                alerts.append(alert)

        # --- price_war: عدة منافسين خفّضوا أسعارهم بشكل كبير (>10%) ---
        significant_drops = 0
        for store, new_price in current.competitor_prices.items():
            old_price = previous.competitor_prices.get(store)
            if old_price is not None and old_price > 0:
                drop_pct = (old_price - new_price) / old_price * 100
                if drop_pct > 10:
                    significant_drops += 1

        if significant_drops >= 2:
            alert = PriceAlert(
                alert_id=uuid.uuid4().hex[:12],
                product_name=product_name,
                alert_type="price_war",
                severity="critical",
                message=f"🔴 حرب أسعار! {significant_drops} منافسين خفّضوا أسعارهم بأكثر من 10%",
                old_value=previous.avg_competitor_price,
                new_value=current.avg_competitor_price,
                change_pct=round(
                    ((current.avg_competitor_price - previous.avg_competitor_price)
                     / previous.avg_competitor_price * 100)
                    if previous.avg_competitor_price > 0 else 0, 2
                ),
                created_at=now,
            )
            alerts.append(alert)

        # --- opportunity: كل المنافسين أسعارهم أعلى ---
        if (current.competitor_prices
                and all(p > current.our_price for p in current.competitor_prices.values() if p > 0)
                and current.price_position == "cheapest"):
            gap = current.avg_competitor_price - current.our_price
            alert = PriceAlert(
                alert_id=uuid.uuid4().hex[:12],
                product_name=product_name,
                alert_type="opportunity",
                severity="info",
                message=f"💡 فرصة لرفع السعر! كل المنافسين أعلى بمتوسط {gap:.2f}",
                old_value=current.our_price,
                new_value=current.avg_competitor_price,
                change_pct=round(
                    (gap / current.our_price * 100) if current.our_price > 0 else 0, 2
                ),
                created_at=now,
            )
            alerts.append(alert)

        # --- gap_widening: الفجوة مع السوق تتّسع ---
        if previous.gap_pct != 0 and current.gap_pct != 0:
            gap_change = abs(current.gap_pct) - abs(previous.gap_pct)
            if gap_change > 5:
                alert = PriceAlert(
                    alert_id=uuid.uuid4().hex[:12],
                    product_name=product_name,
                    alert_type="gap_widening",
                    severity="warning",
                    message=f"📊 الفجوة السعرية تتّسع: كانت {previous.gap_pct:.1f}% أصبحت {current.gap_pct:.1f}%",
                    old_value=previous.gap_pct,
                    new_value=current.gap_pct,
                    change_pct=round(gap_change, 2),
                    created_at=now,
                )
                alerts.append(alert)

        # حفظ التنبيهات
        for a in alerts:
            self._store_alert(a)

        return alerts

    # ────────────────── ملخصات واستعلامات ──────────────────

    def get_position_summary(self) -> dict[str, int]:
        """يعيد عدد المنتجات في كل موقع سعري.

        Returns:
            قاموس {cheapest: N, competitive: M, expensive: K, most_expensive: L}.
        """
        summary: dict[str, int] = {
            "cheapest": 0,
            "competitive": 0,
            "expensive": 0,
            "most_expensive": 0,
        }

        # نجلب آخر لقطة لكل منتج
        rows = self._db.query("""
            SELECT price_position, COUNT(*) as cnt
            FROM (
                SELECT product_name, price_position,
                       ROW_NUMBER() OVER (PARTITION BY product_name ORDER BY created_at DESC) as rn
                FROM price_snapshots
            )
            WHERE rn = 1
            GROUP BY price_position
        """)

        for row in rows:
            pos = row["price_position"]
            if pos in summary:
                summary[pos] = int(row["cnt"])

        return summary

    def get_recent_snapshots(
        self,
        *,
        product_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[PriceSnapshot]:
        """يسترجع آخر اللقطات السعرية.

        Args:
            product_name: تصفية حسب المنتج (اختياري).
            limit: الحد الأقصى للنتائج.

        Returns:
            قائمة لقطات مرتّبة من الأحدث للأقدم.
        """
        if product_name:
            rows = self._db.query(
                "SELECT * FROM price_snapshots WHERE product_name = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (product_name, limit),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM price_snapshots ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_snapshot(r) for r in rows]

    def get_recent_alerts(
        self,
        *,
        severity: Optional[str] = None,
        limit: int = 20,
    ) -> list[PriceAlert]:
        """يسترجع آخر التنبيهات السعرية.

        Args:
            severity: تصفية حسب الخطورة (اختياري).
            limit: الحد الأقصى للنتائج.

        Returns:
            قائمة تنبيهات مرتّبة من الأحدث للأقدم.
        """
        if severity:
            rows = self._db.query(
                "SELECT * FROM price_alerts WHERE severity = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (severity, limit),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM price_alerts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_alert(r) for r in rows]

    # ────────────────── التقارير ──────────────────

    def generate_report(self, products: list[dict]) -> MonitoringReport:
        """يولّد تقرير مراقبة شامل.

        يأخذ لقطات لجميع المنتجات ويكتشف التغيرات ويعيد تقريراً موحّداً.

        Args:
            products: قائمة قواميس المنتجات
                      {product_name, brand, our_price, competitor_prices}.

        Returns:
            تقرير مراقبة شامل.
        """
        snapshots = self.take_batch_snapshot(products)

        all_alerts: list[PriceAlert] = []
        for prod in products:
            alerts = self.detect_changes(prod["product_name"])
            all_alerts.extend(alerts)

        cheapest = sum(1 for s in snapshots if s.price_position == "cheapest")
        competitive = sum(1 for s in snapshots if s.price_position == "competitive")
        expensive = sum(1 for s in snapshots if s.price_position == "expensive")

        gap_values = [s.gap_pct for s in snapshots if s.avg_competitor_price > 0]
        avg_gap = (sum(gap_values) / len(gap_values)) if gap_values else 0.0

        return MonitoringReport(
            report_id=uuid.uuid4().hex[:12],
            total_products=len(products),
            products_monitored=len(snapshots),
            alerts_generated=len(all_alerts),
            cheapest_count=cheapest,
            competitive_count=competitive,
            expensive_count=expensive,
            avg_gap_pct=round(avg_gap, 2),
            snapshots=snapshots,
            alerts=all_alerts,
            created_at=_utcnow(),
        )

    # ────────────────── أدوات داخلية ──────────────────

    @staticmethod
    def _classify_position(
        our_price: float,
        avg_comp: float,
        min_comp: float,
        max_comp: float,
    ) -> str:
        """يصنّف موقعنا السعري مقارنة بالمنافسين.

        - cheapest: سعرنا <= أقل سعر منافس
        - competitive: سعرنا بين الأقل والمتوسط * 1.1
        - expensive: سعرنا > المتوسط * 1.1
        - most_expensive: سعرنا > أعلى سعر منافس
        """
        if min_comp <= 0 and max_comp <= 0:
            return "competitive"
        if our_price <= min_comp:
            return "cheapest"
        if max_comp > 0 and our_price > max_comp:
            return "most_expensive"
        if avg_comp > 0 and our_price > avg_comp * 1.1:
            return "expensive"
        return "competitive"

    def _store_snapshot(self, snap: PriceSnapshot) -> None:
        """يحفظ اللقطة في قاعدة البيانات."""
        self._db.execute("""
            INSERT OR REPLACE INTO price_snapshots
                (snapshot_id, product_name, brand, our_price, competitor_prices,
                 avg_competitor_price, min_competitor_price, max_competitor_price,
                 price_position, gap_pct, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snap.snapshot_id,
            snap.product_name,
            snap.brand,
            snap.our_price,
            json.dumps(snap.competitor_prices, ensure_ascii=False),
            snap.avg_competitor_price,
            snap.min_competitor_price,
            snap.max_competitor_price,
            snap.price_position,
            snap.gap_pct,
            snap.created_at.isoformat(),
        ))

    def _store_alert(self, alert: PriceAlert) -> None:
        """يحفظ التنبيه في قاعدة البيانات."""
        self._db.execute("""
            INSERT OR REPLACE INTO price_alerts
                (alert_id, product_name, alert_type, severity,
                 message, old_value, new_value, change_pct, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.alert_id,
            alert.product_name,
            alert.alert_type,
            alert.severity,
            alert.message,
            alert.old_value,
            alert.new_value,
            alert.change_pct,
            alert.created_at.isoformat(),
        ))

    @staticmethod
    def _row_to_snapshot(r) -> PriceSnapshot:
        """يحوّل صف قاعدة البيانات إلى كائن لقطة."""
        comp_raw = r["competitor_prices"] or "{}"
        comp = json.loads(comp_raw) if isinstance(comp_raw, str) else {}
        return PriceSnapshot(
            snapshot_id=r["snapshot_id"],
            product_name=r["product_name"],
            brand=r["brand"] or "",
            our_price=float(r["our_price"] or 0),
            competitor_prices=comp,
            avg_competitor_price=float(r["avg_competitor_price"] or 0),
            min_competitor_price=float(r["min_competitor_price"] or 0),
            max_competitor_price=float(r["max_competitor_price"] or 0),
            price_position=r["price_position"] or "competitive",
            gap_pct=float(r["gap_pct"] or 0),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    @staticmethod
    def _row_to_alert(r) -> PriceAlert:
        """يحوّل صف قاعدة البيانات إلى كائن تنبيه."""
        return PriceAlert(
            alert_id=r["alert_id"],
            product_name=r["product_name"],
            alert_type=r["alert_type"],
            severity=r["severity"] or "info",
            message=r["message"] or "",
            old_value=float(r["old_value"] or 0),
            new_value=float(r["new_value"] or 0),
            change_pct=float(r["change_pct"] or 0),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
