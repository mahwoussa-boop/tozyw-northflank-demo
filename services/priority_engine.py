"""services/priority_engine.py — محرك الأولوية: ترتيب «الأهم أولاً» لقسم «سعر أعلى» فقط.

MVP لوحدة ج4 (docs/خطة-التطوير-الشاملة.md §ج4 + docs/مقترح-محرك-الأولوية.md):
درجة الإلحاح = الأثر المالي × يقين الطلب × ثقة المصدر، ببوابة يقين لا يتصدّر
دونها عنصر. **قسم سعر أعلى فقط** — بقية الأقسام تبقى بترتيبها الحالي
(``ui.components.criticality.priority_sorted``) بلا أي تغيير.

الأثر المالي يُحسب **وقت العرض** من سعرنا الحيّ (يتغيّر بين الجولات) × سعر المنافس؛
يقين الطلب وثقة المصدر يُحسبان **نهاية كل جولة كشط** في كاش ``product_priority_scores``
(بطيئا التغيّر، مكلفا الحساب نسبياً — تجميع أحداث + متوسط ثقة متاجر) فيبقى فتح
الصفحة قراءة مفهرسة فورية (بوّابة الأداء ≤150ms).

مصادر مُعاد استخدامها بلا بناء من الصفر (توثيق القرار — انظر البحث الذي سبق هذا الملف):
  • ``product_signal_events`` (event='rating_up') — يقين الطلب. يكتبه فعلاً
    ``engines/mahally_scraper.py::_log_signal_events`` منذ جلسات سابقة (44K+ صف)؛
    قارئان حيّان آخران (``alert_service``، ``volatility_service``) يثبتان أنه
    أنبوب حيّ لا ميت — هذه الوحدة قارئها الثالث.
  • ``competitor_stores.trust_score`` — ثقة مصدر مهلي نفسها
    (``services/store_profile_service.py``)، مجلوبة ومخزّنة فعلاً لكنها غير
    مستخدَمة في أي قرار حتى الآن.
  • ``services/monitoring/anomaly_detector.py::AnomalyDetectionEngine.detect`` —
    كشف الأسعار الشاذة إحصائياً (z-score)، يُستدعى هنا **لكل منتج على حدة**
    (استدعاء جديد معزول، لا يمسّ استدعاء ``bootstrap.py`` الخام القائم لغرض آخر).

كل شيء fail-open: أي خطأ/غياب بيانات ⇒ عنصر لا يتصدّر (يبقى بترتيبه الحالي)،
لا يكسر القسم أبداً ولا يُخفي منتجاً.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_TABLE = "product_priority_scores"

# ── ثوابت شفّافة قابلة للضبط (لا أرقام سحرية مدفونة) ──────────────────────────
DEMAND_WINDOW_DAYS = 14        # نافذة أحداث rating_up (تتفق مع HOT_DAYS في volatility_service)
FULL_CERTAINTY_EVENTS = 5.0    # 5+ أحداث ارتفاع تقييم حديثة = يقين طلب كامل (1.0)
CERTAINTY_GATE = 0.30          # يقين_الطلب × ثقة_المصدر ≥ هذا كي يتصدّر القسم
NEUTRAL_TRUST = 0.8            # ثقة محايدة عند غياب تقييم المتجر (لا نعاقب غياب بيانات)
ANOMALY_SENSITIVITY = 2.0      # نفس حساسية anomaly_detector الافتراضية (z-score)
_MIN_PRICES_FOR_ANOMALY = 3    # أقل عدد أسعار لتفعيل كشف الشذوذ (إحصائياً غير موثوق دون هذا)


def _default_db() -> str:
    from utils.data_paths import get_data_db_path
    return get_data_db_path("pricing_v18.db")


# ══════════════════════ دوال نقية (قابلة للاختبار بلا قاعدة) ══════════════════════

def financial_impact(our_price, comp_price, rating_count: int = 0) -> float:
    """قيمة الفجوة السعرية (ر.س) مرجّحة بمؤشر حجم طلب خام. 0 إن لم نكن أغلى فعلاً. خالص."""
    try:
        our_price = float(our_price)
        comp_price = float(comp_price)
    except (TypeError, ValueError):
        return 0.0
    if our_price <= 0 or comp_price <= 0 or our_price <= comp_price:
        return 0.0
    gap = our_price - comp_price
    volume_proxy = 1.0 + min(max(float(rating_count or 0), 0.0), 200.0) / 200.0  # ∈[1,2]
    return gap * volume_proxy


def demand_certainty(rating_up_events: int) -> float:
    """يقارب 1 مع تراكم أحداث ارتفاع تقييم حديثة؛ 0 = لا إشارة طلب مثبتة. خالص."""
    if not rating_up_events or rating_up_events <= 0:
        return 0.0
    return min(float(rating_up_events) / FULL_CERTAINTY_EVENTS, 1.0)


def source_trust(trust_scores: list, outlier_fraction: float = 0.0) -> float:
    """متوسط ثقة متاجر المصدر [0,1] (مقياس مهلي ÷5) مخصوماً بنسبة أسعارها الشاذة. خالص."""
    if not trust_scores:
        base = NEUTRAL_TRUST
    else:
        norm = [min(max(float(t) / 5.0, 0.0), 1.0) for t in trust_scores]
        base = sum(norm) / len(norm)
    outlier_fraction = min(max(float(outlier_fraction), 0.0), 1.0)
    return max(base * (1.0 - outlier_fraction), 0.0)


def passes_certainty_gate(demand: float, trust: float) -> bool:
    """بوابة اليقين: فقط العناصر عالية اليقين تتصدّر القسم؛ ما دونها بترتيبه الطبيعي. خالص."""
    return (demand * trust) >= CERTAINTY_GATE


def compute_urgency(our_price, comp_price, rating_count, demand: float, trust: float) -> float:
    """درجة الإلحاح = الأثر المالي × يقين الطلب × ثقة المصدر. خالص."""
    return financial_impact(our_price, comp_price, rating_count) * demand * trust


def reason_line(our_price, comp_price, rating_up_events: int) -> str:
    """سطر سبب عربي مفهوم لصاحب متجر غير تقني (لا مصطلحات). خالص."""
    try:
        gap = float(our_price) - float(comp_price)
    except (TypeError, ValueError):
        gap = 0.0
    parts = []
    if gap > 0:
        parts.append(f"أغلى بـ{gap:.0f} ر.س من أرخص منافس متوفر")
    if rating_up_events and rating_up_events > 0:
        parts.append(f"طلب متزايد ({int(rating_up_events)} تقييماً جديداً مؤخراً)")
    return " + ".join(parts)


def _outlier_fraction(prices: list) -> float:
    """نسبة الأسعار الشاذة إحصائياً بين منافسي منتج واحد. مُطعَّمة try/except، 0 عند أي عطل."""
    if len(prices) < _MIN_PRICES_FOR_ANOMALY:
        return 0.0
    try:
        from services.monitoring.anomaly_detector import AnomalyDetectionEngine
        anomalies = AnomalyDetectionEngine(db=None).detect(prices, sensitivity=ANOMALY_SENSITIVITY)
        return len(anomalies) / len(prices)
    except Exception:
        return 0.0


# ══════════════════════ الكاش (نهاية كل جولة كشط — عرض فقط، إضافة صرفة) ══════════════════════

def rebuild_product_priority_scores(db_path: Optional[str] = None) -> int:
    """يعيد بناء ``product_priority_scores`` (يقين الطلب + ثقة المصدر فقط — لا سعرنا).

    DROP+CREATE+INSERT ذرّي (BEGIN صريح: DDL لا تنضمّ للمعاملة ضمنياً على py3.14،
    نفس إصلاح ``rebuild_top_competitor``/``rebuild_opportunity_scores``). أي خطأ
    ⇒ 0 والكاش القديم يبقى سليماً (لا فقدان).
    """
    db_path = db_path or _default_db()
    try:
        ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        try:
            comp_rows = ro.execute(
                "SELECT norm_name, competitor, price FROM competitor_products_store "
                "WHERE availability = 1 AND price > 0"
            ).fetchall()
            demand_map = {
                r["norm_name"]: r["c"] for r in ro.execute(
                    "SELECT norm_name, COUNT(*) AS c FROM product_signal_events "
                    "WHERE event = 'rating_up' AND run_at >= datetime('now', ?) "
                    "GROUP BY norm_name",
                    (f"-{DEMAND_WINDOW_DAYS} days",),
                ).fetchall()
            }
            trust_map = {
                r["store_name"]: r["trust_score"] for r in ro.execute(
                    "SELECT store_name, trust_score FROM competitor_stores"
                ).fetchall()
            }
        finally:
            ro.close()
    except Exception as exc:
        logger.warning("rebuild_product_priority_scores: تعذّرت القراءة: %s", exc)
        return 0

    by_product: dict = {}
    for r in comp_rows:
        by_product.setdefault(r["norm_name"], []).append(r)

    ts = datetime.now(timezone.utc).isoformat()
    out_rows = []
    for nn, items in by_product.items():
        prices = [it["price"] for it in items]
        t_scores = [trust_map[it["competitor"]] for it in items if it["competitor"] in trust_map]
        outl = _outlier_fraction(prices)
        n_events = demand_map.get(nn, 0)
        demand = demand_certainty(n_events)
        trust = source_trust(t_scores, outl)
        out_rows.append((nn, demand, trust, n_events, ts))

    if not out_rows:
        return 0

    try:
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                conn.execute("BEGIN")
                conn.execute(f"DROP TABLE IF EXISTS {_TABLE}")
                conn.execute(
                    f"CREATE TABLE {_TABLE} ("
                    "norm_name TEXT PRIMARY KEY, demand_certainty REAL, source_trust REAL, "
                    "rating_up_events INTEGER, rebuilt_at TEXT)"
                )
                conn.executemany(f"INSERT INTO {_TABLE} VALUES (?, ?, ?, ?, ?)", out_rows)
            return len(out_rows)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("rebuild_product_priority_scores: تعذّرت الكتابة: %s", exc)
        return 0


# ══════════════════════ القراءة/الترتيب (وقت العرض — عرض فقط) ══════════════════════

def _load_scores(db_path: Optional[str] = None) -> dict:
    """يحمّل كامل الكاش مرّة واحدة إلى قاموس {norm_name: (demand, trust, n_events)}. mode=ro."""
    db_path = db_path or _default_db()
    try:
        ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = ro.execute(
                f"SELECT norm_name, demand_certainty, source_trust, rating_up_events FROM {_TABLE}"
            ).fetchall()
            return {r[0]: (r[1], r[2], r[3]) for r in rows}
        finally:
            ro.close()
    except Exception:
        return {}


def urgency_sorted(df, db_path: Optional[str] = None):
    """يرتّب قسم «سعر أعلى» بدرجة الإلحاح الحقيقية؛ ما دون بوابة اليقين يبقى بترتيبه الحالي.

    عرض فقط — لا يمسّ ``df`` المصدر. أي خطأ ⇒ يُعيد نتيجة ``priority_sorted`` القائمة
    (سلوك اليوم تماماً) فلا يكسر القسم أبداً. يضيف عمود عرض ``_سبب_الأولوية``
    (فارغ لما لم يتصدّر — لا شيء يُخفى، فقط يُعاد الترتيب).
    """
    import pandas as pd
    from conf.constants import COL_COMP_NAME, COL_COMP_PRICE, COL_OUR_PRICE
    from ui.components.criticality import priority_sorted
    from utils.db_manager import _normalize_for_store

    if not isinstance(df, pd.DataFrame) or df.empty:
        return df

    base = priority_sorted(df)  # الترتيب الحالي — مرجع لما لا يتصدّر ببوابة اليقين
    try:
        scores = _load_scores(db_path)
        if not scores:
            return base

        gated: list[int] = []
        rest: list[int] = []
        urgencies: dict[int, float] = {}
        reasons: dict[int, str] = {}
        for pos in range(len(base)):
            row = base.iloc[pos]
            our_price = row.get(COL_OUR_PRICE, 0)
            comp_price = row.get(COL_COMP_PRICE, 0)
            nn = _normalize_for_store(str(row.get(COL_COMP_NAME, "") or ""))
            demand, trust, n_events = scores.get(nn, (0.0, NEUTRAL_TRUST, 0))
            if passes_certainty_gate(demand, trust):
                urgencies[pos] = compute_urgency(our_price, comp_price, 0, demand, trust)
                reasons[pos] = reason_line(our_price, comp_price, n_events)
                gated.append(pos)
            else:
                rest.append(pos)

        if not gated:
            return base

        gated.sort(key=lambda p: urgencies[p], reverse=True)
        order = gated + rest
        out = base.iloc[order].reset_index(drop=True)
        out["_سبب_الأولوية"] = [reasons.get(p, "") for p in order]
        return out
    except Exception as exc:
        logger.debug("urgency_sorted تعذّر (يُكمل بالترتيب الحالي): %s", exc)
        return base


def movers_count(df, db_path: Optional[str] = None) -> int:
    """عدد العناصر التي تجاوزت بوّابة اليقين (لشريط «⚡ تحرّكات اليوم»). آمن كلياً، 0 عند أي عطل."""
    import pandas as pd
    from conf.constants import COL_COMP_NAME
    from utils.db_manager import _normalize_for_store

    if not isinstance(df, pd.DataFrame) or df.empty:
        return 0
    try:
        scores = _load_scores(db_path)
        if not scores:
            return 0
        n = 0
        for _, row in df.iterrows():
            nn = _normalize_for_store(str(row.get(COL_COMP_NAME, "") or ""))
            demand, trust, _ = scores.get(nn, (0.0, NEUTRAL_TRUST, 0))
            if passes_certainty_gate(demand, trust):
                n += 1
        return n
    except Exception:
        return 0
