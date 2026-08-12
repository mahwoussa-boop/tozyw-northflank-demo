"""services/volatility_service.py — حرارة المنافسين (جدولة كشط تتنفّس مع السوق).

يحوّل ``product_signal_events`` (سجل تحوّلات السعر/التوفّر الذي يكتبه
``mahally_scraper`` عند كل كشط) إلى «درجة حرارة» لكل منافس خلال نافذة
``HOT_DAYS``: المتجر الذي تتحرك أسعاره كثيراً يُكشط **أولاً** (لو انقطع الكشط
تكون أسخن البيانات قد تحدّثت)، والمتجر الراكد يتأخر.

نفس أنماط ``opportunity_service`` المجرَّبة حرفياً:
  • طبقة نقيّة (``rank_heat`` / ``order_by_heat``) بلا قاعدة — تُختبر مباشرة.
  • جدول مشتق ``competitor_heat`` يُعاد بناؤه ذرّياً (DELETE+INSERT) عند نهاية
    الكشط الكامل — **ليس جدولاً أرشيفياً**، كاش مشتق يجوز مسحه.
  • القراءة ``mode=ro``؛ غياب الجدول/القاعدة ⇒ نتيجة فارغة (لا كسر أبداً).
  • فشل أي خطوة لا يمسّ الكشط نفسه (fail-open بترتيب القائمة الأصلي).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("VolatilityService")

# نافذة الحساب بالأيام — أحداث أقدم لا تدلّ على سلوك المتجر الحالي.
HOT_DAYS = 14

# أوزان الدرجة (شفّافة قابلة للضبط — نفس نمط W_* في opportunity_service):
#   الأحداث = كثافة حركة الأسعار؛ الانتشار = كم منتجاً مختلفاً يتحرك.
W_EVENTS = 0.7
W_SPREAD = 0.3

# عتبات الطبقات على الدرجة المطبَّعة [0,1] نسبةً إلى أسخن متجر.
TIER_HOT = 0.50    # ≥ حار: يُكشط أولاً
TIER_WARM = 0.15   # ≥ دافئ؛ دونه راكد

_AGG_SQL = """
SELECT competitor,
       COUNT(*)                  AS n_events,
       COUNT(DISTINCT norm_name) AS n_products,
       MAX(run_at)               AS last_event
FROM product_signal_events
WHERE run_at >= datetime('now', ?)
  AND event IN ('price_up', 'price_down', 'stock_out')
GROUP BY competitor
"""

_DDL = """CREATE TABLE IF NOT EXISTS competitor_heat (
    competitor TEXT PRIMARY KEY,
    n_events INTEGER, n_products INTEGER,
    score REAL, tier TEXT, last_event TEXT, rebuilt_at TEXT
)"""


# ── الطبقة النقيّة ───────────────────────────────────────────────────────────
def rank_heat(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """درجة حرارة مطبَّعة [0,1] لكل منافس + طبقة (حار/دافئ/راكد). دالة خالصة.

    التطبيع نسبي إلى أعلى قيمة في المجموعة (متين أمام تغيّر حجم البيانات —
    نفس مبدأ ``score_row``). قائمة فارغة ⇒ فارغة.
    """
    if not rows:
        return []
    max_ev = max(int(r.get("n_events") or 0) for r in rows) or 1
    max_pr = max(int(r.get("n_products") or 0) for r in rows) or 1
    out: list[dict[str, Any]] = []
    for r in rows:
        ev = int(r.get("n_events") or 0)
        pr = int(r.get("n_products") or 0)
        score = W_EVENTS * (ev / max_ev) + W_SPREAD * (pr / max_pr)
        tier = "حار" if score >= TIER_HOT else ("دافئ" if score >= TIER_WARM else "راكد")
        out.append({
            "competitor": str(r.get("competitor") or ""),
            "n_events": ev, "n_products": pr,
            "score": round(score, 4), "tier": tier,
            "last_event": r.get("last_event"),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def order_by_heat(
    comps: list, heat: dict[str, float],
    name_of: Callable[[Any], str] = lambda c: getattr(c, "name", str(c)),
) -> list:
    """يعيد ترتيب قائمة كشط حسب الحرارة تنازلياً (دالة خالصة — لا قاعدة).

    منافس **بلا درجة** (جديد/بلا أحداث بعد) يتقدّم الجميع — يحتاج خط أساس
    قبل أن يُحكم عليه. درجات متساوية تحفظ الترتيب الأصلي (sort مستقر).
    """
    if not heat:
        return list(comps)
    return sorted(comps, key=lambda c: -heat.get(name_of(c), float("inf")))


# ── القاعدة: بناء الجدول المشتق وقراءته ──────────────────────────────────────
def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return str(db_path)
    from utils.data_paths import get_data_db_path
    return get_data_db_path("pricing_v18.db")


def rebuild_competitor_heat(db_path: Optional[str] = None) -> int:
    """يعيد بناء ``competitor_heat`` من أحداث آخر ``HOT_DAYS`` يوماً (ذرّي).
    يعيد عدد الصفوف؛ أي خطأ ⇒ 0 بتحذير (لا يمسّ الكشط أبداً)."""
    try:
        path = _db_path(db_path)
        ro = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in ro.execute(_AGG_SQL, (f"-{HOT_DAYS} days",))]
        finally:
            ro.close()
        ranked = rank_heat(rows)
        ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute(_DDL)
            with conn:
                conn.execute("DELETE FROM competitor_heat")
                conn.executemany(
                    "INSERT INTO competitor_heat (competitor, n_events, n_products,"
                    " score, tier, last_event, rebuilt_at) VALUES (?,?,?,?,?,?,?)",
                    [(r["competitor"], r["n_events"], r["n_products"], r["score"],
                      r["tier"], r["last_event"], ts) for r in ranked])
            return len(ranked)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("إعادة بناء حرارة المنافسين فشلت (تجاهل): %s", exc)
        return 0


def heat_order(db_path: Optional[str] = None) -> dict[str, float]:
    """يقرأ درجات الحرارة {منافس: درجة} من الجدول المشتق (``mode=ro``).
    جدول/قاعدة غائبة ⇒ ``{}`` (الكشط يمضي بترتيبه الأصلي — fail-open)."""
    try:
        path = _db_path(db_path)
        ro = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            return {str(r[0]): float(r[1]) for r in
                    ro.execute("SELECT competitor, score FROM competitor_heat")}
        finally:
            ro.close()
    except Exception:
        return {}
