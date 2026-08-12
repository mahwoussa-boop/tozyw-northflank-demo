"""services/monitoring/anomaly_detector.py — كشف الشذوذ السعري."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from infrastructure.db_manager import DatabaseManager


@dataclass
class PriceAnomaly:
    """شذوذ سعري مكتشف."""
    anomaly_id: str
    product_name: str
    competitor: str
    expected_price: float
    actual_price: float
    deviation_pct: float
    anomaly_type: str   # sudden_drop | sudden_spike | outlier | data_error
    confidence: float
    severity: str       # info | warning | critical
    detected_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "product_name": self.product_name,
            "competitor": self.competitor,
            "expected_price": self.expected_price,
            "actual_price": self.actual_price,
            "deviation_pct": self.deviation_pct,
            "anomaly_type": self.anomaly_type,
            "confidence": self.confidence,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PriceAnomaly":
        det = d.get("detected_at", "")
        if isinstance(det, str):
            det = datetime.fromisoformat(det)
        return cls(
            anomaly_id=d["anomaly_id"],
            product_name=d["product_name"],
            competitor=d.get("competitor", ""),
            expected_price=float(d.get("expected_price", 0)),
            actual_price=float(d.get("actual_price", 0)),
            deviation_pct=float(d.get("deviation_pct", 0)),
            anomaly_type=d.get("anomaly_type", "outlier"),
            confidence=float(d.get("confidence", 0)),
            severity=d.get("severity", "info"),
            detected_at=det,
        )


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


class AnomalyDetectionEngine:
    """يكتشف تغيرات سعرية مفاجئة وغير طبيعية."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def ensure_table(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS price_anomalies (
                anomaly_id   TEXT PRIMARY KEY,
                product_name TEXT NOT NULL,
                competitor   TEXT DEFAULT '',
                expected_price REAL,
                actual_price   REAL,
                deviation_pct  REAL,
                anomaly_type   TEXT,
                confidence     REAL,
                severity       TEXT,
                detected_at    TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_anom_severity ON price_anomalies(severity)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_anom_detected ON price_anomalies(detected_at)")

    def detect(
        self,
        prices: list[float],
        *,
        product_name: str = "",
        competitor: str = "",
        sensitivity: float = 2.0,
    ) -> list[PriceAnomaly]:
        """يكتشف الشذوذ باستخدام z-score."""
        if not prices:
            return []

        anomalies: list[PriceAnomaly] = []
        from core.product_entity import _utcnow
        now = _utcnow()
        mean = _mean(prices)
        std = _std(prices)

        for price in prices:
            atype: Optional[str] = None

            if price <= 0:
                atype = "data_error"
            elif std > 0 and abs(price - mean) / std > sensitivity:
                dev_pct = ((price - mean) / mean * 100) if mean else 0
                if dev_pct < -15:
                    atype = "sudden_drop"
                elif dev_pct > 15:
                    atype = "sudden_spike"
                else:
                    atype = "outlier"

            if atype is not None:
                dev_pct = ((price - mean) / mean * 100) if mean else 0
                sev = self.classify_severity(dev_pct)
                conf = min(abs(price - mean) / std, 1.0) if std > 0 else 0.8
                anomalies.append(PriceAnomaly(
                    anomaly_id=uuid.uuid4().hex[:12],
                    product_name=product_name,
                    competitor=competitor,
                    expected_price=round(mean, 2),
                    actual_price=price,
                    deviation_pct=round(dev_pct, 2),
                    anomaly_type=atype,
                    confidence=round(min(conf, 1.0), 4),
                    severity=sev,
                    detected_at=now,
                ))
        return anomalies

    def detect_from_dataframe(
        self, df, *,
        price_col: str = "السعر",
        name_col: str = "المنتج",
        competitor_col: str = "المنافس",
        sensitivity: float = 2.0,
    ) -> list[PriceAnomaly]:
        """يفحص DataFrame للعثور على شذوذ."""
        if df is None or df.empty:
            return []
        results: list[PriceAnomaly] = []
        if price_col not in df.columns:
            return results
        has_name = name_col in df.columns
        has_comp = competitor_col in df.columns
        if has_name:
            for name, group in df.groupby(name_col):
                prices = group[price_col].dropna().tolist()
                comp = str(group[competitor_col].iloc[0]) if has_comp else ""
                results.extend(self.detect(
                    prices, product_name=str(name),
                    competitor=comp, sensitivity=sensitivity,
                ))
        else:
            prices = df[price_col].dropna().tolist()
            results.extend(self.detect(prices, sensitivity=sensitivity))
        return results

    def classify_severity(self, deviation_pct: float) -> str:
        absv = abs(deviation_pct)
        if absv > 50:
            return "critical"
        if absv > 20:
            return "warning"
        return "info"

    def is_data_error(self, price: float, *, context_prices: list[float]) -> bool:
        if price <= 0:
            return True
        if not context_prices:
            return False
        std = _std(context_prices)
        mean = _mean(context_prices)
        if std > 0 and abs(price - mean) / std > 10:
            return True
        return False

    def record_anomaly(self, anomaly: PriceAnomaly) -> None:
        self._db.execute("""
            INSERT OR REPLACE INTO price_anomalies
                (anomaly_id, product_name, competitor, expected_price,
                 actual_price, deviation_pct, anomaly_type, confidence,
                 severity, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            anomaly.anomaly_id, anomaly.product_name, anomaly.competitor,
            anomaly.expected_price, anomaly.actual_price,
            anomaly.deviation_pct, anomaly.anomaly_type,
            anomaly.confidence, anomaly.severity,
            anomaly.detected_at.isoformat(),
        ))

    def get_recent(self, *, limit: int = 50) -> list[PriceAnomaly]:
        rows = self._db.query(
            "SELECT * FROM price_anomalies ORDER BY detected_at DESC LIMIT ?", (limit,))
        return [self._row_to_anomaly(r) for r in rows]

    def get_by_severity(self, severity: str, *, limit: int = 50) -> list[PriceAnomaly]:
        rows = self._db.query(
            "SELECT * FROM price_anomalies WHERE severity = ? ORDER BY detected_at DESC LIMIT ?",
            (severity, limit))
        return [self._row_to_anomaly(r) for r in rows]

    @staticmethod
    def _row_to_anomaly(r) -> PriceAnomaly:
        return PriceAnomaly(
            anomaly_id=r["anomaly_id"], product_name=r["product_name"],
            competitor=r["competitor"] or "", expected_price=float(r["expected_price"] or 0),
            actual_price=float(r["actual_price"] or 0), deviation_pct=float(r["deviation_pct"] or 0),
            anomaly_type=r["anomaly_type"] or "outlier", confidence=float(r["confidence"] or 0),
            severity=r["severity"] or "info", detected_at=datetime.fromisoformat(r["detected_at"]),
        )
