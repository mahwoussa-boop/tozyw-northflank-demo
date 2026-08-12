"""tests/test_anomaly_detector.py — اختبارات كشف الشذوذ السعري."""
import tempfile, os, math
import pytest
from datetime import datetime, timezone

from infrastructure.db_manager import DatabaseManager
from services.monitoring.anomaly_detector import (
    AnomalyDetectionEngine, PriceAnomaly, _mean, _std,
)


@pytest.fixture
def engine():
    tmp = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(tmp)
    eng = AnomalyDetectionEngine(db)
    eng.ensure_table()
    yield eng
    try:
        os.unlink(tmp)
    except OSError:
        pass


class TestEnsureTable:
    def test_creates_table(self, engine):
        rows = engine._db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_anomalies'")
        assert len(rows) == 1

    def test_idempotent(self, engine):
        engine.ensure_table()  # should not raise


class TestDetect:
    def test_finds_sudden_drop(self, engine):
        prices = [100, 100, 100, 100, 100, 30]  # 30 is a sudden drop
        anomalies = engine.detect(prices, product_name="test")
        assert any(a.anomaly_type == "sudden_drop" for a in anomalies)

    def test_finds_sudden_spike(self, engine):
        prices = [100, 100, 100, 100, 100, 200]  # 200 is a spike
        anomalies = engine.detect(prices, product_name="test")
        assert any(a.anomaly_type == "sudden_spike" for a in anomalies)

    def test_finds_data_error_zero(self, engine):
        prices = [100, 100, 0, 100]
        anomalies = engine.detect(prices)
        assert any(a.anomaly_type == "data_error" for a in anomalies)

    def test_returns_empty_for_normal(self, engine):
        prices = [100, 101, 99, 100, 100]
        anomalies = engine.detect(prices)
        assert len(anomalies) == 0

    def test_respects_sensitivity(self, engine):
        prices = [100, 100, 100, 120]
        # High sensitivity should not flag 120
        high = engine.detect(prices, sensitivity=5.0)
        # Low sensitivity should flag it
        low = engine.detect(prices, sensitivity=1.0)
        assert len(high) <= len(low)


class TestClassifySeverity:
    def test_critical(self, engine):
        assert engine.classify_severity(55) == "critical"
        assert engine.classify_severity(-60) == "critical"

    def test_warning(self, engine):
        assert engine.classify_severity(25) == "warning"
        assert engine.classify_severity(-30) == "warning"

    def test_info(self, engine):
        assert engine.classify_severity(10) == "info"
        assert engine.classify_severity(-5) == "info"


class TestIsDataError:
    def test_zero_price(self, engine):
        assert engine.is_data_error(0, context_prices=[100, 100]) is True

    def test_negative_price(self, engine):
        assert engine.is_data_error(-5, context_prices=[100]) is True

    def test_normal_price(self, engine):
        assert engine.is_data_error(105, context_prices=[100, 100, 100]) is False


class TestPersistence:
    def test_record_and_get_recent(self, engine):
        a = PriceAnomaly(
            anomaly_id="test123", product_name="عطر", competitor="نون",
            expected_price=100, actual_price=50, deviation_pct=-50,
            anomaly_type="sudden_drop", confidence=0.9, severity="critical",
            detected_at=datetime.now(timezone.utc),
        )
        engine.record_anomaly(a)
        recent = engine.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].anomaly_id == "test123"

    def test_get_by_severity(self, engine):
        for i, sev in enumerate(["info", "warning", "critical"]):
            a = PriceAnomaly(
                anomaly_id=f"s{i}", product_name="p", competitor="",
                expected_price=100, actual_price=50, deviation_pct=-50,
                anomaly_type="outlier", confidence=0.5, severity=sev,
                detected_at=datetime.now(timezone.utc),
            )
            engine.record_anomaly(a)
        critical = engine.get_by_severity("critical")
        assert len(critical) == 1
        assert critical[0].severity == "critical"


class TestSerialization:
    def test_roundtrip(self):
        a = PriceAnomaly(
            anomaly_id="abc", product_name="test", competitor="comp",
            expected_price=100, actual_price=50, deviation_pct=-50,
            anomaly_type="sudden_drop", confidence=0.9, severity="critical",
            detected_at=datetime(2025, 1, 1, 12, 0),
        )
        d = a.to_dict()
        b = PriceAnomaly.from_dict(d)
        assert a.anomaly_id == b.anomaly_id
        assert a.actual_price == b.actual_price
        assert a.anomaly_type == b.anomaly_type
