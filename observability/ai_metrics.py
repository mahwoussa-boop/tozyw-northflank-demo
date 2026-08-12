"""observability/ai_metrics.py — جامع مقاييس استدعاءات الذكاء الاصطناعي.

يتتبع أداء وتكلفة ودقة وتأخير استدعاءات API للذكاء الاصطناعي:
- تسجيل كل استدعاء مع معلومات المزوّد، النموذج، التوكنات، والتكلفة.
- احتساب إحصائيات يومية (تكلفة، توكنات).
- تحليل أداء كل مزوّد (نسبة نجاح، متوسط تأخير).
- حساب مئويات التأخير (p50–p99).
- اتجاه التكلفة اليومي.
- نسبة الأخطاء ونسبة ضربات الكاش.
- إحصائيات لكل عملية (تصنيف، تسعير، تحليل...).
- لوحة بيانات شاملة للمراقبة.

⚠️ لا يستورد Streamlit أبداً.
"""
from __future__ import annotations

from core.product_entity import _utcnow

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from infrastructure.db_manager import DatabaseManager


# ════════════════════════════════════════════════════════════════════
#  كيان المقياس
# ════════════════════════════════════════════════════════════════════

@dataclass
class AICallMetrics:
    """سجل مقياس لاستدعاء واحد لخدمة ذكاء اصطناعي.

    يحفظ كل التفاصيل المطلوبة لتحليل التكلفة والأداء:
    - معلومات المزوّد والنموذج.
    - عدد التوكنات (prompt + completion = total).
    - زمن التأخير بالميلي ثانية.
    - التكلفة بالدولار.
    - حالة النجاح/الفشل مع رسالة الخطأ.
    - نوع العملية (تصنيف، تسعير، تحليل...).
    """

    metric_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    success: bool = True
    cached: bool = False
    error_message: str = ""
    operation: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """تحويل لقاموس للتخزين في قاعدة البيانات."""
        return {
            "metric_id": self.metric_id,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "success": self.success,
            "cached": self.cached,
            "error_message": self.error_message,
            "operation": self.operation,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AICallMetrics":
        """بناء كائن مقياس من قاموس مُخزَّن.

        يتعامل مع تحويل أنواع البيانات بشكل آمن:
        - ``created_at`` يُحلَّل من نصّ ISO أو يُستخدم الوقت الحالي.
        - ``success`` و ``cached`` تُحوَّلان من int أو bool.
        """
        ts = d.get("created_at", "")
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
            metric_id=d.get("metric_id", uuid.uuid4().hex[:12]),
            provider=d.get("provider", ""),
            model=d.get("model", ""),
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            latency_ms=float(d.get("latency_ms", 0.0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            success=bool(d.get("success", True)),
            cached=bool(d.get("cached", False)),
            error_message=d.get("error_message", ""),
            operation=d.get("operation", ""),
            created_at=parsed_ts,
        )


# ════════════════════════════════════════════════════════════════════
#  جامع المقاييس
# ════════════════════════════════════════════════════════════════════

class AIMetricsCollector:
    """خدمة جمع وتحليل مقاييس استدعاءات الذكاء الاصطناعي.

    تُحقَن عبر المُنشئ بكائن ``DatabaseManager`` لفصل الاهتمامات.
    توفّر واجهة واحدة لتسجيل المقاييس واستعلامها وتحليلها.

    الاستخدام النموذجي::

        collector = AIMetricsCollector(db)
        collector.ensure_table()
        m = collector.record(provider="openrouter", model="gpt-4o", ...)
        daily = collector.get_daily_cost()
    """

    _CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS ai_call_metrics (
        metric_id        TEXT PRIMARY KEY,
        provider         TEXT NOT NULL,
        model            TEXT DEFAULT '',
        prompt_tokens    INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens     INTEGER DEFAULT 0,
        latency_ms       REAL DEFAULT 0,
        cost_usd         REAL DEFAULT 0,
        success          INTEGER DEFAULT 1,
        cached           INTEGER DEFAULT 0,
        error_message    TEXT DEFAULT '',
        operation        TEXT DEFAULT '',
        created_at       TEXT NOT NULL
    )
    """

    _CREATE_INDEXES_SQL = [
        "CREATE INDEX IF NOT EXISTS idx_aim_provider ON ai_call_metrics(provider)",
        "CREATE INDEX IF NOT EXISTS idx_aim_created ON ai_call_metrics(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_aim_operation ON ai_call_metrics(operation)",
    ]

    def __init__(self, db: DatabaseManager) -> None:
        """يهيّئ الجامع بمدير قاعدة بيانات.

        Args:
            db: مدير قاعدة بيانات SQLite للقراءة والكتابة.
        """
        self._db = db

    # ── إنشاء الجدول ───────────────────────────────────────────

    def ensure_table(self) -> None:
        """ينشئ جدول ``ai_call_metrics`` والفهارس إن لم يكونا موجودين.

        يُستخدم عند بدء التطبيق أو أول استخدام للخدمة. آمن
        للاستدعاء المتكرر بفضل ``CREATE TABLE IF NOT EXISTS``.
        """
        self._db.execute(self._CREATE_TABLE_SQL)
        for idx_sql in self._CREATE_INDEXES_SQL:
            self._db.execute(idx_sql)

    # ── تسجيل مقياس ────────────────────────────────────────────

    def record(
        self,
        *,
        provider: str,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        success: bool = True,
        cached: bool = False,
        error_message: str = "",
        operation: str = "",
    ) -> AICallMetrics:
        """ينشئ سجل مقياس ويخزّنه في قاعدة البيانات.

        يحسب ``total_tokens`` تلقائياً من prompt + completion.

        Args:
            provider: اسم المزوّد (openrouter | gemini | cohere).
            model: اسم النموذج المستخدم.
            prompt_tokens: عدد توكنات المدخلات.
            completion_tokens: عدد توكنات المخرجات.
            latency_ms: زمن التأخير بالميلي ثانية.
            cost_usd: التكلفة بالدولار الأمريكي.
            success: هل نجح الاستدعاء؟
            cached: هل كان من الكاش؟
            error_message: رسالة الخطأ إن وُجد.
            operation: نوع العملية (classify | price | analyze | ...).

        Returns:
            كائن ``AICallMetrics`` المُنشأ.
        """
        total = prompt_tokens + completion_tokens
        metrics = AICallMetrics(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            success=success,
            cached=cached,
            error_message=error_message,
            operation=operation,
        )

        self._db.execute(
            """
            INSERT INTO ai_call_metrics
                (metric_id, provider, model, prompt_tokens, completion_tokens,
                 total_tokens, latency_ms, cost_usd, success, cached,
                 error_message, operation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.metric_id,
                metrics.provider,
                metrics.model,
                metrics.prompt_tokens,
                metrics.completion_tokens,
                metrics.total_tokens,
                metrics.latency_ms,
                metrics.cost_usd,
                1 if metrics.success else 0,
                1 if metrics.cached else 0,
                metrics.error_message,
                metrics.operation,
                metrics.created_at.isoformat(),
            ),
        )
        return metrics

    # ── استعلامات التكلفة اليومية ──────────────────────────────

    def get_daily_cost(self, *, date: Optional[date] = None) -> float:
        """يحسب إجمالي تكلفة الاستدعاءات ليوم محدد بالدولار.

        Args:
            date: التاريخ المطلوب. إن لم يُحدَّد يُستخدم اليوم.

        Returns:
            إجمالي التكلفة بالدولار (0.0 إن لم توجد بيانات).
        """
        target = date or _today()
        date_str = target.isoformat()
        row = self._db.query_one(
            """
            SELECT COALESCE(SUM(cost_usd), 0.0) AS total
            FROM ai_call_metrics
            WHERE created_at >= ? AND created_at < ?
            """,
            (date_str + "T00:00:00", date_str + "T23:59:59.999999"),
        )
        return float(row["total"]) if row else 0.0

    # ── استعلامات التوكنات اليومية ─────────────────────────────

    def get_daily_tokens(self, *, date: Optional[date] = None) -> dict[str, int]:
        """يحسب إجمالي التوكنات ليوم محدد.

        Args:
            date: التاريخ المطلوب. إن لم يُحدَّد يُستخدم اليوم.

        Returns:
            قاموس بمفاتيح: prompt_tokens, completion_tokens, total.
        """
        target = date or _today()
        date_str = target.isoformat()
        row = self._db.query_one(
            """
            SELECT
                COALESCE(SUM(prompt_tokens), 0) AS pt,
                COALESCE(SUM(completion_tokens), 0) AS ct,
                COALESCE(SUM(total_tokens), 0) AS tt
            FROM ai_call_metrics
            WHERE created_at >= ? AND created_at < ?
            """,
            (date_str + "T00:00:00", date_str + "T23:59:59.999999"),
        )
        if row:
            return {
                "prompt_tokens": int(row["pt"]),
                "completion_tokens": int(row["ct"]),
                "total": int(row["tt"]),
            }
        return {"prompt_tokens": 0, "completion_tokens": 0, "total": 0}

    # ── إحصائيات المزوّدين ─────────────────────────────────────

    def get_provider_stats(self, *, days: int = 30) -> list[dict[str, Any]]:
        """يحسب إحصائيات أداء كل مزوّد خلال فترة محددة.

        Args:
            days: عدد الأيام للرجوع (افتراضي 30).

        Returns:
            قائمة بقاموس لكل مزوّد يحتوي:
            provider, total_calls, success_rate, avg_latency_ms,
            total_cost, total_tokens.
        """
        cutoff = _days_ago_iso(days)
        rows = self._db.query(
            """
            SELECT
                provider,
                COUNT(*)                            AS total_calls,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok,
                AVG(latency_ms)                     AS avg_lat,
                COALESCE(SUM(cost_usd), 0.0)        AS total_cost,
                COALESCE(SUM(total_tokens), 0)       AS total_tokens
            FROM ai_call_metrics
            WHERE created_at >= ?
            GROUP BY provider
            ORDER BY total_calls DESC
            """,
            (cutoff,),
        )
        results: list[dict[str, Any]] = []
        for r in rows:
            total = int(r["total_calls"])
            ok = int(r["ok"])
            results.append({
                "provider": r["provider"],
                "total_calls": total,
                "success_rate": round(ok / total * 100, 2) if total > 0 else 0.0,
                "avg_latency_ms": round(float(r["avg_lat"]), 2) if r["avg_lat"] else 0.0,
                "total_cost": round(float(r["total_cost"]), 6),
                "total_tokens": int(r["total_tokens"]),
            })
        return results

    # ── مئويات التأخير ─────────────────────────────────────────

    def get_latency_percentiles(self, *, days: int = 7) -> dict[str, float]:
        """يحسب مئويات التأخير (p50, p75, p90, p95, p99).

        يستخدم الترتيب واختيار الفهرس بدلاً من numpy.

        Args:
            days: عدد الأيام للرجوع (افتراضي 7).

        Returns:
            قاموس {p50, p75, p90, p95, p99} بالميلي ثانية.
            يعيد أصفار إن لم توجد بيانات.
        """
        cutoff = _days_ago_iso(days)
        rows = self._db.query(
            """
            SELECT latency_ms
            FROM ai_call_metrics
            WHERE created_at >= ?
            ORDER BY latency_ms
            """,
            (cutoff,),
        )
        values = [float(r["latency_ms"]) for r in rows]
        if not values:
            return {"p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}

        return {
            f"p{p}": _percentile(values, p) for p in (50, 75, 90, 95, 99)
        }

    # ── اتجاه التكلفة ─────────────────────────────────────────

    def get_cost_trend(self, *, days: int = 30) -> list[dict[str, Any]]:
        """يحسب اتجاه التكلفة اليومي لفترة محددة.

        Args:
            days: عدد الأيام للرجوع (افتراضي 30).

        Returns:
            قائمة يومية [{date, cost_usd, call_count}] مرتبة بالتاريخ.
        """
        cutoff = _days_ago_iso(days)
        rows = self._db.query(
            """
            SELECT
                DATE(created_at) AS day,
                COALESCE(SUM(cost_usd), 0.0) AS cost,
                COUNT(*) AS cnt
            FROM ai_call_metrics
            WHERE created_at >= ?
            GROUP BY DATE(created_at)
            ORDER BY day
            """,
            (cutoff,),
        )
        return [
            {
                "date": r["day"],
                "cost_usd": round(float(r["cost"]), 6),
                "call_count": int(r["cnt"]),
            }
            for r in rows
        ]

    # ── نسبة الأخطاء ──────────────────────────────────────────

    def get_error_rate(self, *, days: int = 7) -> float:
        """يحسب نسبة الاستدعاءات الفاشلة (بالمئة).

        Args:
            days: عدد الأيام للرجوع (افتراضي 7).

        Returns:
            نسبة مئوية (0.0 – 100.0). يعيد 0.0 إن لم توجد بيانات.
        """
        cutoff = _days_ago_iso(days)
        row = self._db.query_one(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fails
            FROM ai_call_metrics
            WHERE created_at >= ?
            """,
            (cutoff,),
        )
        if not row or int(row["total"]) == 0:
            return 0.0
        return round(int(row["fails"]) / int(row["total"]) * 100, 2)

    # ── نسبة ضربات الكاش ───────────────────────────────────────

    def get_cache_hit_rate(self, *, days: int = 7) -> float:
        """يحسب نسبة الاستدعاءات المخدّمة من الكاش (بالمئة).

        Args:
            days: عدد الأيام للرجوع (افتراضي 7).

        Returns:
            نسبة مئوية (0.0 – 100.0). يعيد 0.0 إن لم توجد بيانات.
        """
        cutoff = _days_ago_iso(days)
        row = self._db.query_one(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) AS hits
            FROM ai_call_metrics
            WHERE created_at >= ?
            """,
            (cutoff,),
        )
        if not row or int(row["total"]) == 0:
            return 0.0
        return round(int(row["hits"]) / int(row["total"]) * 100, 2)

    # ── إحصائيات العمليات ──────────────────────────────────────

    def get_operation_stats(self, *, days: int = 30) -> list[dict[str, Any]]:
        """يحسب إحصائيات لكل نوع عملية (تصنيف، تسعير...).

        Args:
            days: عدد الأيام للرجوع (افتراضي 30).

        Returns:
            قائمة لكل عملية: {operation, call_count, avg_latency, total_cost}.
        """
        cutoff = _days_ago_iso(days)
        rows = self._db.query(
            """
            SELECT
                operation,
                COUNT(*)                        AS call_count,
                AVG(latency_ms)                 AS avg_lat,
                COALESCE(SUM(cost_usd), 0.0)    AS total_cost
            FROM ai_call_metrics
            WHERE created_at >= ?
            GROUP BY operation
            ORDER BY call_count DESC
            """,
            (cutoff,),
        )
        return [
            {
                "operation": r["operation"],
                "call_count": int(r["call_count"]),
                "avg_latency": round(float(r["avg_lat"]), 2) if r["avg_lat"] else 0.0,
                "total_cost": round(float(r["total_cost"]), 6),
            }
            for r in rows
        ]

    # ── لوحة البيانات ──────────────────────────────────────────

    def get_dashboard_data(self) -> dict[str, Any]:
        """يجمع كل البيانات المطلوبة للوحة مراقبة شاملة.

        Returns:
            قاموس يحتوي:
            - daily_cost: تكلفة اليوم.
            - daily_tokens: توكنات اليوم.
            - provider_stats: إحصائيات المزوّدين (30 يوم).
            - latency_p95: مئوية التأخير 95 (7 أيام).
            - error_rate: نسبة الأخطاء (7 أيام).
            - cache_hit_rate: نسبة ضربات الكاش (7 أيام).
            - cost_trend_7d: اتجاه التكلفة (7 أيام).
        """
        percentiles = self.get_latency_percentiles(days=7)
        return {
            "daily_cost": self.get_daily_cost(),
            "daily_tokens": self.get_daily_tokens(),
            "provider_stats": self.get_provider_stats(days=30),
            "latency_p95": percentiles.get("p95", 0.0),
            "error_rate": self.get_error_rate(days=7),
            "cache_hit_rate": self.get_cache_hit_rate(days=7),
            "cost_trend_7d": self.get_cost_trend(days=7),
        }


# ════════════════════════════════════════════════════════════════════
#  دوال مساعدة (خاصة بالوحدة)
# ════════════════════════════════════════════════════════════════════

def _today() -> date:
    """يعيد تاريخ اليوم (UTC)."""
    return _utcnow().date()


def _days_ago_iso(days: int) -> str:
    """يعيد التاريخ قبل ``days`` يوم بتنسيق ISO مع بداية اليوم."""
    from datetime import timedelta
    d = _utcnow() - timedelta(days=days)
    return d.date().isoformat() + "T00:00:00"


def _percentile(sorted_values: list[float], p: int) -> float:
    """يحسب المئوية ``p`` لقائمة مرتبة بدون numpy.

    يستخدم طريقة الاستيفاء الخطي (linear interpolation) لحساب
    المئوية بدقة حتى مع أحجام عينات صغيرة.

    Args:
        sorted_values: قائمة قيم مرتبة تصاعدياً (غير فارغة).
        p: المئوية المطلوبة (0–100).

    Returns:
        قيمة المئوية مقرّبة لرقمين عشريين.
    """
    n = len(sorted_values)
    if n == 1:
        return round(sorted_values[0], 2)
    # موقع المئوية (0-indexed)
    k = (p / 100.0) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_values[int(k)], 2)
    # استيفاء خطي
    d = k - f
    return round(sorted_values[f] + d * (sorted_values[c] - sorted_values[f]), 2)
