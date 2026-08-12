"""tests/test_ai_metrics.py — اختبارات شاملة لجامع مقاييس الذكاء الاصطناعي.

يغطي 15+ حالة اختبار:
- إنشاء الجدول والفهارس.
- تسجيل المقاييس واسترجاعها.
- التكلفة والتوكنات اليومية.
- إحصائيات المزوّدين والعمليات.
- مئويات التأخير.
- اتجاه التكلفة.
- نسبة الأخطاء وضربات الكاش.
- لوحة البيانات الشاملة.
- تسلسل/إلغاء تسلسل AICallMetrics.
- تتبع مزوّدين متعددين بشكل منفصل.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from infrastructure.db_manager import DatabaseManager
from observability.ai_metrics import AICallMetrics, AIMetricsCollector


# ════════════════════════════════════════════════════════════════════
#  تجهيزات الاختبار
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def db(tmp_path):
    """ينشئ قاعدة بيانات مؤقتة لكل اختبار."""
    db_path = str(tmp_path / "test_metrics.db")
    manager = DatabaseManager(db_path)
    yield manager
    manager.close()


@pytest.fixture
def collector(db):
    """ينشئ جامع مقاييس مع جدول جاهز."""
    c = AIMetricsCollector(db)
    c.ensure_table()
    return c


def _insert_metric(collector: AIMetricsCollector, **overrides) -> AICallMetrics:
    """مساعد لإدراج مقياس مع قيم افتراضية."""
    defaults = {
        "provider": "openrouter",
        "model": "gpt-4o",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "latency_ms": 500.0,
        "cost_usd": 0.01,
        "success": True,
        "cached": False,
        "error_message": "",
        "operation": "classify",
    }
    defaults.update(overrides)
    return collector.record(**defaults)


# ════════════════════════════════════════════════════════════════════
#  الاختبارات
# ════════════════════════════════════════════════════════════════════

class TestEnsureTable:
    """اختبارات إنشاء الجدول."""

    def test_ensure_table_creates_table(self, db):
        """ensure_table ينشئ جدول ai_call_metrics."""
        collector = AIMetricsCollector(db)
        assert not db.table_exists("ai_call_metrics")
        collector.ensure_table()
        assert db.table_exists("ai_call_metrics")

    def test_ensure_table_idempotent(self, collector):
        """استدعاء ensure_table أكثر من مرة لا يسبب خطأ."""
        collector.ensure_table()  # مرة ثانية
        collector.ensure_table()  # مرة ثالثة
        assert collector._db.table_exists("ai_call_metrics")


class TestRecord:
    """اختبارات تسجيل المقاييس."""

    def test_record_creates_and_returns_metrics(self, collector):
        """record يُنشئ سجلاً ويعيد كائن AICallMetrics."""
        m = _insert_metric(collector)
        assert isinstance(m, AICallMetrics)
        assert m.provider == "openrouter"
        assert m.model == "gpt-4o"
        assert m.prompt_tokens == 100
        assert m.completion_tokens == 50
        assert m.total_tokens == 150
        assert m.cost_usd == 0.01
        assert m.success is True
        assert len(m.metric_id) == 12

    def test_record_stores_in_database(self, collector, db):
        """record يخزّن السجل في قاعدة البيانات."""
        m = _insert_metric(collector)
        row = db.query_one(
            "SELECT * FROM ai_call_metrics WHERE metric_id = ?",
            (m.metric_id,),
        )
        assert row is not None
        assert row["provider"] == "openrouter"
        assert row["total_tokens"] == 150

    def test_record_calculates_total_tokens(self, collector):
        """record يحسب total_tokens = prompt + completion."""
        m = collector.record(
            provider="gemini",
            prompt_tokens=200,
            completion_tokens=300,
        )
        assert m.total_tokens == 500


class TestDailyCost:
    """اختبارات التكلفة اليومية."""

    def test_get_daily_cost_returns_correct_total(self, collector):
        """get_daily_cost يعيد مجموع التكلفة الصحيح لليوم."""
        _insert_metric(collector, cost_usd=0.05)
        _insert_metric(collector, cost_usd=0.03)
        _insert_metric(collector, cost_usd=0.02)
        cost = collector.get_daily_cost()
        assert abs(cost - 0.10) < 1e-6

    def test_get_daily_cost_with_no_data_returns_zero(self, collector):
        """get_daily_cost بدون بيانات يعيد 0."""
        cost = collector.get_daily_cost()
        assert cost == 0.0


class TestDailyTokens:
    """اختبارات التوكنات اليومية."""

    def test_get_daily_tokens_returns_correct_counts(self, collector):
        """get_daily_tokens يعيد العد الصحيح للتوكنات."""
        _insert_metric(collector, prompt_tokens=100, completion_tokens=50)
        _insert_metric(collector, prompt_tokens=200, completion_tokens=100)
        tokens = collector.get_daily_tokens()
        assert tokens["prompt_tokens"] == 300
        assert tokens["completion_tokens"] == 150
        assert tokens["total"] == 450


class TestProviderStats:
    """اختبارات إحصائيات المزوّدين."""

    def test_get_provider_stats_groups_by_provider(self, collector):
        """get_provider_stats يجمّع حسب المزوّد."""
        _insert_metric(collector, provider="openrouter")
        _insert_metric(collector, provider="openrouter")
        _insert_metric(collector, provider="gemini")
        stats = collector.get_provider_stats(days=1)
        providers = {s["provider"]: s for s in stats}
        assert "openrouter" in providers
        assert "gemini" in providers
        assert providers["openrouter"]["total_calls"] == 2
        assert providers["gemini"]["total_calls"] == 1

    def test_get_provider_stats_calculates_success_rate(self, collector):
        """get_provider_stats يحسب نسبة النجاح بشكل صحيح."""
        _insert_metric(collector, provider="cohere", success=True)
        _insert_metric(collector, provider="cohere", success=True)
        _insert_metric(collector, provider="cohere", success=False)
        stats = collector.get_provider_stats(days=1)
        cohere = [s for s in stats if s["provider"] == "cohere"][0]
        # 2/3 = 66.67%
        assert abs(cohere["success_rate"] - 66.67) < 0.1


class TestLatencyPercentiles:
    """اختبارات مئويات التأخير."""

    def test_get_latency_percentiles_returns_all(self, collector):
        """get_latency_percentiles يعيد كل المئويات."""
        for lat in [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]:
            _insert_metric(collector, latency_ms=float(lat))
        p = collector.get_latency_percentiles(days=1)
        assert "p50" in p
        assert "p75" in p
        assert "p90" in p
        assert "p95" in p
        assert "p99" in p
        # p50 should be around 500-600 (median of 100..1000)
        assert p["p50"] > 0
        assert p["p90"] >= p["p50"]

    def test_get_latency_percentiles_empty(self, collector):
        """get_latency_percentiles بدون بيانات يعيد أصفار."""
        p = collector.get_latency_percentiles(days=1)
        assert p == {"p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}


class TestCostTrend:
    """اختبارات اتجاه التكلفة."""

    def test_get_cost_trend_returns_daily_data(self, collector):
        """get_cost_trend يعيد بيانات يومية."""
        _insert_metric(collector, cost_usd=0.05)
        _insert_metric(collector, cost_usd=0.03)
        trend = collector.get_cost_trend(days=1)
        assert len(trend) >= 1
        day_entry = trend[0]
        assert "date" in day_entry
        assert "cost_usd" in day_entry
        assert "call_count" in day_entry
        assert day_entry["call_count"] == 2
        assert abs(day_entry["cost_usd"] - 0.08) < 1e-6


class TestErrorRate:
    """اختبارات نسبة الأخطاء."""

    def test_get_error_rate_calculates_correctly(self, collector):
        """get_error_rate يحسب النسبة الصحيحة."""
        _insert_metric(collector, success=True)
        _insert_metric(collector, success=True)
        _insert_metric(collector, success=False)
        _insert_metric(collector, success=False)
        rate = collector.get_error_rate(days=1)
        assert abs(rate - 50.0) < 0.1

    def test_get_error_rate_no_data(self, collector):
        """get_error_rate بدون بيانات يعيد 0."""
        rate = collector.get_error_rate(days=1)
        assert rate == 0.0


class TestCacheHitRate:
    """اختبارات نسبة ضربات الكاش."""

    def test_get_cache_hit_rate_calculates_correctly(self, collector):
        """get_cache_hit_rate يحسب النسبة الصحيحة."""
        _insert_metric(collector, cached=True)
        _insert_metric(collector, cached=True)
        _insert_metric(collector, cached=True)
        _insert_metric(collector, cached=False)
        rate = collector.get_cache_hit_rate(days=1)
        assert abs(rate - 75.0) < 0.1


class TestOperationStats:
    """اختبارات إحصائيات العمليات."""

    def test_get_operation_stats_groups_by_operation(self, collector):
        """get_operation_stats يجمّع حسب العملية."""
        _insert_metric(collector, operation="classify")
        _insert_metric(collector, operation="classify")
        _insert_metric(collector, operation="price")
        _insert_metric(collector, operation="analyze")
        stats = collector.get_operation_stats(days=1)
        ops = {s["operation"]: s for s in stats}
        assert ops["classify"]["call_count"] == 2
        assert ops["price"]["call_count"] == 1
        assert ops["analyze"]["call_count"] == 1


class TestDashboardData:
    """اختبارات لوحة البيانات."""

    def test_get_dashboard_data_returns_complete_dict(self, collector):
        """get_dashboard_data يعيد قاموساً شاملاً بكل المفاتيح."""
        _insert_metric(collector)
        data = collector.get_dashboard_data()
        assert "daily_cost" in data
        assert "daily_tokens" in data
        assert "provider_stats" in data
        assert "latency_p95" in data
        assert "error_rate" in data
        assert "cache_hit_rate" in data
        assert "cost_trend_7d" in data
        # يجب أن يكون daily_tokens قاموساً
        assert isinstance(data["daily_tokens"], dict)
        assert isinstance(data["provider_stats"], list)


class TestAICallMetricsSerialization:
    """اختبارات التسلسل وإلغاء التسلسل."""

    def test_to_dict_from_dict_roundtrip(self):
        """to_dict/from_dict تحافظان على كل البيانات."""
        original = AICallMetrics(
            metric_id="abc123def456",
            provider="gemini",
            model="gemini-pro",
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            latency_ms=1234.56,
            cost_usd=0.042,
            success=False,
            cached=True,
            error_message="rate_limited",
            operation="swarm_analyst",
            created_at=datetime(2026, 1, 15, 10, 30, 0),
        )
        d = original.to_dict()
        restored = AICallMetrics.from_dict(d)
        assert restored.metric_id == original.metric_id
        assert restored.provider == original.provider
        assert restored.model == original.model
        assert restored.prompt_tokens == original.prompt_tokens
        assert restored.completion_tokens == original.completion_tokens
        assert restored.total_tokens == original.total_tokens
        assert abs(restored.latency_ms - original.latency_ms) < 0.01
        assert abs(restored.cost_usd - original.cost_usd) < 1e-6
        assert restored.success == original.success
        assert restored.cached == original.cached
        assert restored.error_message == original.error_message
        assert restored.operation == original.operation
        assert restored.created_at == original.created_at


class TestMultipleProviders:
    """اختبارات تتبع مزوّدين متعددين."""

    def test_multiple_providers_tracked_separately(self, collector):
        """المزوّدون المختلفون يُتتبعون بشكل منفصل."""
        _insert_metric(collector, provider="openrouter", cost_usd=0.10)
        _insert_metric(collector, provider="openrouter", cost_usd=0.20)
        _insert_metric(collector, provider="gemini", cost_usd=0.05)
        _insert_metric(collector, provider="cohere", cost_usd=0.03)

        stats = collector.get_provider_stats(days=1)
        by_provider = {s["provider"]: s for s in stats}

        assert len(by_provider) == 3
        assert abs(by_provider["openrouter"]["total_cost"] - 0.30) < 1e-6
        assert abs(by_provider["gemini"]["total_cost"] - 0.05) < 1e-6
        assert abs(by_provider["cohere"]["total_cost"] - 0.03) < 1e-6

    def test_provider_latency_independent(self, collector):
        """متوسط التأخير لكل مزوّد مستقل."""
        _insert_metric(collector, provider="openrouter", latency_ms=100.0)
        _insert_metric(collector, provider="openrouter", latency_ms=300.0)
        _insert_metric(collector, provider="gemini", latency_ms=1000.0)

        stats = collector.get_provider_stats(days=1)
        by_provider = {s["provider"]: s for s in stats}

        assert abs(by_provider["openrouter"]["avg_latency_ms"] - 200.0) < 0.1
        assert abs(by_provider["gemini"]["avg_latency_ms"] - 1000.0) < 0.1
