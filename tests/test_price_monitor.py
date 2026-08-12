"""tests/test_price_monitor.py — اختبارات وحدة مراقب الأسعار المستمر.

يختبر جميع وظائف ContinuousPriceMonitor بما في ذلك اللقطات والتنبيهات والتقارير.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.db_manager import DatabaseManager
from services.monitoring.price_monitor import (
    ContinuousPriceMonitor,
    MonitoringReport,
    PriceAlert,
    PriceSnapshot,
)


@pytest.fixture
def db(tmp_path):
    """ينشئ قاعدة بيانات مؤقتة للاختبار."""
    db_path = str(tmp_path / "test_monitor.db")
    manager = DatabaseManager(db_path)
    return manager


@pytest.fixture
def monitor(db):
    """ينشئ مراقب أسعار جاهزاً مع الجداول."""
    mon = ContinuousPriceMonitor(db)
    mon.ensure_table()
    return mon


# ──────────────── اختبارات الجداول ────────────────


class TestEnsureTable:
    def test_creates_both_tables(self, db):
        """ensure_table ينشئ جدولَي price_snapshots و price_alerts."""
        monitor = ContinuousPriceMonitor(db)
        monitor.ensure_table()
        assert db.table_exists("price_snapshots")
        assert db.table_exists("price_alerts")

    def test_idempotent(self, monitor):
        """استدعاء ensure_table مرتين لا يسبّب خطأ."""
        monitor.ensure_table()  # ثاني مرة
        assert True


# ──────────────── اختبارات اللقطات ────────────────


class TestTakeSnapshot:
    def test_creates_snapshot(self, monitor):
        """take_snapshot ينشئ لقطة سعرية."""
        snap = monitor.take_snapshot(
            product_name="منتج أ",
            brand="علامة1",
            our_price=100.0,
            competitor_prices={"متجر1": 110.0, "متجر2": 120.0},
        )
        assert isinstance(snap, PriceSnapshot)
        assert snap.product_name == "منتج أ"
        assert snap.brand == "علامة1"
        assert snap.our_price == 100.0
        assert len(snap.snapshot_id) == 12

    def test_position_cheapest(self, monitor):
        """يصنّف سعرنا كأرخص عندما نكون أقل من الجميع."""
        snap = monitor.take_snapshot(
            product_name="منتج ب",
            our_price=50.0,
            competitor_prices={"أ": 80.0, "ب": 90.0, "ج": 100.0},
        )
        assert snap.price_position == "cheapest"

    def test_position_expensive(self, monitor):
        """يصنّف سعرنا كمكلف عندما نتجاوز المتوسط * 1.1 لكن ضمن النطاق."""
        # avg = (80+100+120)/3 = 100, our = 115 > 100*1.1=110, but < max=120
        snap = monitor.take_snapshot(
            product_name="منتج ج",
            our_price=115.0,
            competitor_prices={"أ": 80.0, "ب": 100.0, "ج": 120.0},
        )
        # avg = 100, our = 115 > max(110) → most_expensive (exceeds all competitors)
        assert snap.price_position in ("expensive", "most_expensive")

    def test_position_most_expensive(self, monitor):
        """يصنّف سعرنا كأغلى عندما نتجاوز كل المنافسين."""
        snap = monitor.take_snapshot(
            product_name="منتج د",
            our_price=200.0,
            competitor_prices={"أ": 90.0, "ب": 100.0, "ج": 110.0},
        )
        assert snap.price_position == "most_expensive"

    def test_position_competitive(self, monitor):
        """يصنّف سعرنا كتنافسي عندما نكون ضمن النطاق."""
        # avg = 100, our = 105 < 110 → competitive, but > min=90
        snap = monitor.take_snapshot(
            product_name="منتج هـ",
            our_price=105.0,
            competitor_prices={"أ": 90.0, "ب": 100.0, "ج": 110.0},
        )
        assert snap.price_position == "competitive"

    def test_gap_pct_calculation(self, monitor):
        """يحسب نسبة الفجوة بشكل صحيح."""
        # avg comp = (100+200)/2 = 150, our = 120
        # gap = (120 - 150) / 150 * 100 = -20.0
        snap = monitor.take_snapshot(
            product_name="فجوة",
            our_price=120.0,
            competitor_prices={"أ": 100.0, "ب": 200.0},
        )
        assert snap.gap_pct == -20.0

    def test_stores_in_db(self, monitor, db):
        """يحفظ اللقطة في قاعدة البيانات."""
        snap = monitor.take_snapshot(
            product_name="db_test",
            our_price=50.0,
            competitor_prices={"x": 60.0},
        )
        row = db.query_one(
            "SELECT * FROM price_snapshots WHERE snapshot_id = ?",
            (snap.snapshot_id,),
        )
        assert row is not None
        assert row["product_name"] == "db_test"
        assert float(row["our_price"]) == 50.0

    def test_competitor_prices_stored_as_json(self, monitor, db):
        """يحفظ أسعار المنافسين بصيغة JSON."""
        comp = {"store_a": 55.0, "store_b": 65.0}
        snap = monitor.take_snapshot(
            product_name="json_test",
            our_price=50.0,
            competitor_prices=comp,
        )
        row = db.query_one(
            "SELECT competitor_prices FROM price_snapshots WHERE snapshot_id = ?",
            (snap.snapshot_id,),
        )
        loaded = json.loads(row["competitor_prices"])
        assert loaded == comp

    def test_empty_competitors(self, monitor):
        """يتعامل بأمان مع قاموس منافسين فارغ."""
        snap = monitor.take_snapshot(
            product_name="فارغ",
            our_price=100.0,
            competitor_prices={},
        )
        assert snap.avg_competitor_price == 0.0
        assert snap.gap_pct == 0.0
        assert snap.price_position == "competitive"


# ──────────────── اختبارات اللقطات الدفعية ────────────────


class TestBatchSnapshot:
    def test_returns_multiple(self, monitor):
        """take_batch_snapshot يعيد لقطات متعددة."""
        products = [
            {"product_name": "أ", "our_price": 100, "competitor_prices": {"x": 110}},
            {"product_name": "ب", "our_price": 200, "competitor_prices": {"x": 190}},
            {"product_name": "ج", "our_price": 300, "competitor_prices": {"x": 310}},
        ]
        snaps = monitor.take_batch_snapshot(products)
        assert len(snaps) == 3
        assert all(isinstance(s, PriceSnapshot) for s in snaps)


# ──────────────── اختبارات كشف التغيرات ────────────────


class TestDetectChanges:
    def test_no_prior_snapshot_returns_empty(self, monitor):
        """يعيد قائمة فارغة عند عدم وجود لقطة سابقة."""
        monitor.take_snapshot(
            product_name="وحيد",
            our_price=100.0,
            competitor_prices={"أ": 110.0},
        )
        alerts = monitor.detect_changes("وحيد")
        assert alerts == []

    def test_detects_competitor_undercut(self, monitor, db):
        """يكتشف تخفيض منافس لسعره تحت سعرنا."""
        # لقطة أولى: المنافس أعلى
        snap1 = monitor.take_snapshot(
            product_name="undercut",
            our_price=100.0,
            competitor_prices={"store_a": 110.0},
        )
        # نعدّل created_at للقطة الأولى لتكون قديمة
        db.execute(
            "UPDATE price_snapshots SET created_at = ? WHERE snapshot_id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), snap1.snapshot_id),
        )

        # لقطة ثانية: المنافس أقل من سعرنا
        monitor.take_snapshot(
            product_name="undercut",
            our_price=100.0,
            competitor_prices={"store_a": 90.0},
        )

        alerts = monitor.detect_changes("undercut")
        types = [a.alert_type for a in alerts]
        assert "competitor_undercut" in types

    def test_detects_opportunity(self, monitor, db):
        """يكتشف فرصة لرفع السعر عندما كل المنافسين أعلى."""
        snap1 = monitor.take_snapshot(
            product_name="فرصة",
            our_price=80.0,
            competitor_prices={"أ": 100.0, "ب": 110.0},
        )
        db.execute(
            "UPDATE price_snapshots SET created_at = ? WHERE snapshot_id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), snap1.snapshot_id),
        )
        monitor.take_snapshot(
            product_name="فرصة",
            our_price=80.0,
            competitor_prices={"أ": 120.0, "ب": 130.0},
        )

        alerts = monitor.detect_changes("فرصة")
        types = [a.alert_type for a in alerts]
        assert "opportunity" in types

    def test_detects_price_war(self, monitor, db):
        """يكتشف حرب أسعار عند تخفيض عدة منافسين بأكثر من 10%."""
        snap1 = monitor.take_snapshot(
            product_name="حرب",
            our_price=100.0,
            competitor_prices={"أ": 120.0, "ب": 130.0, "ج": 140.0},
        )
        db.execute(
            "UPDATE price_snapshots SET created_at = ? WHERE snapshot_id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), snap1.snapshot_id),
        )
        # كل المنافسين خفّضوا >10%
        monitor.take_snapshot(
            product_name="حرب",
            our_price=100.0,
            competitor_prices={"أ": 100.0, "ب": 110.0, "ج": 120.0},
        )

        alerts = monitor.detect_changes("حرب")
        types = [a.alert_type for a in alerts]
        assert "price_war" in types


# ──────────────── اختبارات الملخّص والاستعلامات ────────────────


class TestPositionSummary:
    def test_returns_correct_counts(self, monitor):
        """get_position_summary يعيد العدد الصحيح لكل موقع."""
        # cheapest
        monitor.take_snapshot(
            product_name="رخيص1", our_price=50.0,
            competitor_prices={"أ": 80.0, "ب": 90.0},
        )
        monitor.take_snapshot(
            product_name="رخيص2", our_price=40.0,
            competitor_prices={"أ": 70.0, "ب": 80.0},
        )
        # expensive
        monitor.take_snapshot(
            product_name="غالي1", our_price=200.0,
            competitor_prices={"أ": 90.0, "ب": 100.0, "ج": 110.0},
        )

        summary = monitor.get_position_summary()
        assert summary["cheapest"] >= 2
        assert summary["most_expensive"] >= 1


class TestRecentSnapshots:
    def test_returns_ordered(self, monitor):
        """get_recent_snapshots يعيد اللقطات مرتّبة من الأحدث."""
        monitor.take_snapshot(
            product_name="ترتيب", our_price=100.0,
            competitor_prices={"أ": 110.0},
        )
        time.sleep(0.05)
        monitor.take_snapshot(
            product_name="ترتيب", our_price=105.0,
            competitor_prices={"أ": 115.0},
        )

        snaps = monitor.get_recent_snapshots(product_name="ترتيب")
        assert len(snaps) == 2
        assert snaps[0].our_price == 105.0  # الأحدث أولاً
        assert snaps[1].our_price == 100.0


class TestRecentAlerts:
    def test_filters_by_severity(self, monitor, db):
        """get_recent_alerts يصفّي حسب الخطورة."""
        # ندخل تنبيهات يدوياً
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO price_alerts (alert_id, product_name, alert_type, severity, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("a1", "منتج", "opportunity", "info", "تنبيه", now),
        )
        db.execute(
            "INSERT INTO price_alerts (alert_id, product_name, alert_type, severity, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("a2", "منتج", "price_war", "critical", "حرب", now),
        )

        info_alerts = monitor.get_recent_alerts(severity="info")
        assert len(info_alerts) == 1
        assert info_alerts[0].severity == "info"

        critical_alerts = monitor.get_recent_alerts(severity="critical")
        assert len(critical_alerts) == 1
        assert critical_alerts[0].severity == "critical"


# ──────────────── اختبارات التقارير ────────────────


class TestGenerateReport:
    def test_returns_complete_report(self, monitor):
        """generate_report يعيد تقريراً شاملاً."""
        products = [
            {"product_name": "أ", "our_price": 50, "competitor_prices": {"x": 80}},
            {"product_name": "ب", "our_price": 200, "competitor_prices": {"x": 100, "y": 110}},
        ]
        report = monitor.generate_report(products)
        assert isinstance(report, MonitoringReport)
        assert report.total_products == 2
        assert report.products_monitored == 2
        assert len(report.snapshots) == 2
        assert report.cheapest_count >= 1


# ──────────────── اختبارات كائنات البيانات ────────────────


class TestDataclasses:
    def test_snapshot_to_dict_roundtrip(self):
        """PriceSnapshot.to_dict ينتج قاموساً كاملاً."""
        now = datetime.now(timezone.utc)
        snap = PriceSnapshot(
            snapshot_id="abc123",
            product_name="منتج",
            brand="علامة",
            our_price=100.0,
            competitor_prices={"أ": 110.0},
            avg_competitor_price=110.0,
            min_competitor_price=110.0,
            max_competitor_price=110.0,
            price_position="cheapest",
            gap_pct=-9.09,
            created_at=now,
        )
        d = snap.to_dict()
        assert d["snapshot_id"] == "abc123"
        assert d["product_name"] == "منتج"
        assert d["our_price"] == 100.0
        assert d["competitor_prices"] == {"أ": 110.0}
        assert d["created_at"] == now.isoformat()

    def test_price_alert_creation(self):
        """PriceAlert يُنشأ بكل الحقول."""
        alert = PriceAlert(
            alert_id="xyz789",
            product_name="منتج",
            alert_type="competitor_undercut",
            severity="warning",
            message="تنبيه تجريبي",
            old_value=100.0,
            new_value=80.0,
            change_pct=-20.0,
        )
        assert alert.alert_id == "xyz789"
        assert alert.alert_type == "competitor_undercut"
        assert alert.severity == "warning"
        assert alert.change_pct == -20.0

    def test_monitoring_report_creation(self):
        """MonitoringReport يُنشأ بقيم افتراضية صحيحة."""
        report = MonitoringReport(report_id="rpt001")
        assert report.total_products == 0
        assert report.snapshots == []
        assert report.alerts == []
        assert isinstance(report.created_at, datetime)

    def test_snapshot_default_factory(self):
        """competitor_prices يستخدم default_factory وليس مشترك بين الكائنات."""
        s1 = PriceSnapshot(
            snapshot_id="s1", product_name="أ", brand="", our_price=10.0,
        )
        s2 = PriceSnapshot(
            snapshot_id="s2", product_name="ب", brand="", our_price=20.0,
        )
        s1.competitor_prices["x"] = 99.0
        assert "x" not in s2.competitor_prices
