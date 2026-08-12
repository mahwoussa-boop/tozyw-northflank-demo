"""tests/test_api.py — اختبارات نقاط وصول FastAPI.

يستخدم FastAPI TestClient لاختبار جميع النقاط مع حاوية اعتماديات حقيقية
تعمل على قاعدة بيانات SQLite مؤقتة (:memory:).
يغطي 18 اختبار وحدة.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
pytest.importorskip("fastapi")

# ── التأكد من مسار الاستيراد ───────────────────────────────────
_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))


# ══════════════════════════════════════════════════════════════════
#  بناء حاوية اعتماديات مؤقتة (في الذاكرة)
# ══════════════════════════════════════════════════════════════════

def _build_test_container():
    """يبني حاوية اختبار باستخدام قاعدة بيانات مؤقتة."""
    import tempfile
    from conf.settings import Settings
    from bootstrap import build_container

    settings = Settings.load()
    # Use a temp file DB (not :memory:) so tables persist across connections
    tmp = tempfile.mktemp(suffix=".db")
    settings = settings.model_copy(update={"db_path": tmp})
    return build_container(settings)


# نستبدل get_container قبل استيراد app
_test_container = None


def _get_test_container():
    global _test_container
    if _test_container is None:
        _test_container = _build_test_container()
    return _test_container


# ── تطبيق الاستبدال ──
import api.deps as deps_mod
deps_mod.get_container = _get_test_container

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


# ══════════════════════════════════════════════════════════════════
#  اختبارات Health
# ══════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    """اختبارات نقطة فحص الصحة."""

    def test_health_returns_200(self):
        """GET /api/v1/health يُعيد 200."""
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_returns_ok(self):
        """GET /api/v1/health يحتوي status=ok."""
        data = client.get("/api/v1/health").json()
        assert data["status"] == "ok"
        assert data["version"] == "4.0"
        assert data["tests_count"] == 585


# ══════════════════════════════════════════════════════════════════
#  اختبارات Products
# ══════════════════════════════════════════════════════════════════

class TestProductsEndpoints:
    """اختبارات نقاط المنتجات."""

    def test_products_stats_returns_dict(self):
        """GET /api/v1/products/stats يُعيد قاموساً."""
        resp = client.get("/api/v1/products/stats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_products_list_returns_structure(self):
        """GET /api/v1/products يُعيد هيكل صحيح."""
        resp = client.get("/api/v1/products")
        assert resp.status_code == 200
        data = resp.json()
        assert "products" in data
        assert "count" in data

    def test_product_not_found_returns_404(self):
        """GET /api/v1/products/{invalid} يُعيد 404."""
        resp = client.get("/api/v1/products/nonexistent_id_12345")
        assert resp.status_code == 404

    def test_product_timeline_not_found_returns_404(self):
        """GET /api/v1/products/{invalid}/timeline يُعيد 404."""
        resp = client.get("/api/v1/products/nonexistent_id_12345/timeline")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════
#  اختبارات Notifications
# ══════════════════════════════════════════════════════════════════

class TestNotificationsEndpoints:
    """اختبارات نقاط التنبيهات."""

    def test_notifications_returns_list(self):
        """GET /api/v1/notifications يُعيد قائمة."""
        resp = client.get("/api/v1/notifications")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_notifications_count_returns_int(self):
        """GET /api/v1/notifications/count يُعيد عدداً صحيحاً."""
        resp = client.get("/api/v1/notifications/count")
        assert resp.status_code == 200
        data = resp.json()
        assert "unread" in data
        assert isinstance(data["unread"], int)

    def test_mark_notification_read_invalid_id(self):
        """POST /api/v1/notifications/{invalid}/read يُعيد استجابة مناسبة."""
        resp = client.post("/api/v1/notifications/invalid_id_xyz/read")
        assert resp.status_code == 200
        data = resp.json()
        assert data["notif_id"] == "invalid_id_xyz"
        # mark_read لمعرّف غير موجود يُعيد marked=False
        assert data["marked"] is False


# ══════════════════════════════════════════════════════════════════
#  اختبارات Swarm
# ══════════════════════════════════════════════════════════════════

class TestSwarmEndpoint:
    """اختبارات نقطة تحليل السَّرب."""

    def test_swarm_analyze_returns_decision(self):
        """POST /api/v1/swarm/analyze يُعيد SwarmDecision."""
        body = {
            "product_name": "عطر تيست",
            "brand": "Test",
            "our_price": 100.0,
            "competitor_prices": [95.0, 105.0, 98.0],
            "section": "عطور",
        }
        resp = client.post("/api/v1/swarm/analyze", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "final_price" in data
        assert "final_confidence" in data
        assert "consensus_type" in data


# ══════════════════════════════════════════════════════════════════
#  اختبارات AutoPilot
# ══════════════════════════════════════════════════════════════════

class TestAutoPilotEndpoints:
    """اختبارات نقاط الطيار الآلي."""

    def test_autopilot_status_returns_config(self):
        """GET /api/v1/autopilot/status يُعيد إعدادات."""
        resp = client.get("/api/v1/autopilot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "daily_stats" in data

    def test_autopilot_toggle_changes_enabled(self):
        """POST /api/v1/autopilot/toggle يغيّر حالة التفعيل."""
        resp = client.post("/api/v1/autopilot/toggle", json={"enabled": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True

        # نعيده لحالة التعطيل
        resp2 = client.post("/api/v1/autopilot/toggle", json={"enabled": False})
        assert resp2.json()["enabled"] is False

    def test_autopilot_evaluate_returns_verdict(self):
        """POST /api/v1/autopilot/evaluate يُعيد حكماً."""
        body = {
            "product_name": "عطر تيست",
            "brand": "",
            "current_price": 100.0,
            "suggested_price": 105.0,
            "confidence": 0.95,
            "consensus_type": "unanimous",
        }
        resp = client.post("/api/v1/autopilot/evaluate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "can_execute" in data
        assert "reason" in data
        assert "approval_level" in data


# ══════════════════════════════════════════════════════════════════
#  اختبارات Intelligence
# ══════════════════════════════════════════════════════════════════

class TestIntelligenceEndpoints:
    """اختبارات نقاط الذكاء التسعيري."""

    def test_predict_returns_price_trend(self):
        """POST /api/v1/intelligence/predict يُعيد PriceTrend."""
        body = {
            "prices": [100.0, 102.0, 104.0, 106.0, 108.0],
            "product_name": "عطر تجريبي",
        }
        resp = client.post("/api/v1/intelligence/predict", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "direction" in data
        assert "confidence" in data
        assert "predicted_price_7d" in data

    def test_seasons_returns_list(self):
        """GET /api/v1/intelligence/seasons يُعيد قائمة."""
        resp = client.get("/api/v1/intelligence/seasons")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_anomalies_returns_list(self):
        """GET /api/v1/intelligence/anomalies يُعيد قائمة."""
        resp = client.get("/api/v1/intelligence/anomalies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ══════════════════════════════════════════════════════════════════
#  اختبارات Metrics
# ══════════════════════════════════════════════════════════════════

class TestMetricsEndpoint:
    """اختبارات لوحة المقاييس."""

    def test_metrics_dashboard_returns_data(self):
        """GET /api/v1/metrics/dashboard يُعيد بيانات اللوحة."""
        resp = client.get("/api/v1/metrics/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "product_stats" in data
        assert "unread_notifications" in data
