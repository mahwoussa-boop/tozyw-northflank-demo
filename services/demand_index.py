"""services/demand_index.py — «مؤشر طلب مقدّر» لكل منتج (م7).

يحوّل ``product_signal_events`` (تحوّلات السعر/التوفّر/التقييم التي يكتبها
الكشط) إلى **مؤشر طلب مقدّر** لكل ``norm_name`` خلال نافذة ``WINDOW_DAYS``.

⚠️ ما هذا المؤشر وما ليس هو — قيدٌ صارم لا تجميل:
  • اسمه «مؤشر طلب مقدّر». **ليس** وحدات مباعة، ولا إيراداً، ولا حصة سوقية.
    نحن لا نرى مبيعات أحد؛ نرى آثاراً غير مباشرة عند المنافسين ونقدّر منها.
  • **لا يفعّل تحديث سعر تلقائياً، ولا يكون عاملاً وحيداً لأي قرار.** يمرّ ما
    يُبنى عليه عبر بوّابة الأهلية (``send_quality_guard``) كبقية القرارات.
  • مفتاحه ``norm_name`` ⇒ يرث ضعف الهوية المقيس: 95.5% من المنتجات المُقيَّمة
    يمثّلها **متجر واحد**. لذلك يُعرض ``store_count`` مع كل صفّ، ويجب أن تقول
    الواجهة صراحةً أن المؤشر تقديريّ ومبنيّ على متاجر محدودة.

المصادر الخمسة ونوافذها (كلها من نفس النافذة ``WINDOW_DAYS``):
  • ``rating_up``      تقييم جديد ظهر ⇒ أقوى دليل غير مباشر على **شراء فعلي**.
  • ``stock_out``      نفد المخزون ⇒ الطلب تجاوز العرض.
  • ``back_in_stock``  أُعيد التوريد بعد نفاد ⇒ طلب متكرّر يستحق إعادة التخزين.
  • ``price_up``       البائع يرفع السعر ⇒ ضغط طلب.
  • ``price_down``     البائع يخفض السعر ⇒ إشارة **سالبة** (ضعف طلب).
``first_seen_out`` مستبعَد عمداً: «ظهر نافداً» يخلط منتجاً مطلوباً بمنتج لم
يُورَّد أصلاً — إشارة غامضة لا تُحتسب.

نفس أنماط ``volatility_service``/``opportunity_service`` حرفياً: طبقة نقيّة
تُختبر بلا قاعدة · جدول مشتق يُعاد بناؤه ذرّياً · قراءة ``mode=ro`` · fail-open
(غياب القاعدة/الجدول ⇒ نتيجة فارغة، لا كسر صفحة).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

logger = logging.getLogger("DemandIndex")

# نافذة الحساب بالأيام. الأحداث المتاحة تغطي 33 يوماً متميّزاً (قياس 2026-08-13)،
# فنافذة 30 يوماً تستوعبها كلها تقريباً بلا ادّعاء تاريخ أطول مما نملك.
WINDOW_DAYS = 30

# أوزان المكوّنات — شفّافة وقابلة للضبط (نفس نمط W_* في الخدمات الشقيقة).
# التقييم أعلى وزناً لأنه أقرب ما لدينا إلى دليل شراء فعلي؛ وخفض السعر سالب.
W_RATING_UP = 3.0
W_STOCK_OUT = 2.0
W_BACK_IN_STOCK = 2.5
W_PRICE_UP = 1.0
W_PRICE_DOWN = -1.0

# عتبات الطبقات على الدرجة المطبَّعة [0,100] نسبةً إلى أعلى منتج في النافذة.
TIER_HIGH = 50.0   # ≥ طلب مرتفع (مقدَّر)
TIER_MEDIUM = 15.0  # ≥ طلب متوسط؛ دونه منخفض

# وصف كل مكوّن للعرض: (المفتاح، التسمية، الوزن، مصدره)
COMPONENTS: tuple[tuple[str, str, float, str], ...] = (
    ("rating_up", "تقييمات جديدة", W_RATING_UP, "ظهور تقييم جديد لدى منافس"),
    ("back_in_stock", "إعادة توريد بعد نفاد", W_BACK_IN_STOCK, "عودة التوفّر بعد نفاد"),
    ("stock_out", "نفاد مخزون", W_STOCK_OUT, "تحوّل المنتج إلى نافد لدى منافس"),
    ("price_up", "رفع سعر", W_PRICE_UP, "رفع منافس لسعره"),
    ("price_down", "خفض سعر", W_PRICE_DOWN, "خفض منافس لسعره (إشارة ضعف)"),
)

_AGG_SQL = """
SELECT norm_name,
       COUNT(DISTINCT competitor)                                  AS store_count,
       SUM(CASE WHEN event='rating_up'     THEN 1 ELSE 0 END)      AS rating_up,
       SUM(CASE WHEN event='stock_out'     THEN 1 ELSE 0 END)      AS stock_out,
       SUM(CASE WHEN event='back_in_stock' THEN 1 ELSE 0 END)      AS back_in_stock,
       SUM(CASE WHEN event='price_up'      THEN 1 ELSE 0 END)      AS price_up,
       SUM(CASE WHEN event='price_down'    THEN 1 ELSE 0 END)      AS price_down,
       MAX(run_at)                                                 AS last_event
FROM product_signal_events
WHERE run_at >= datetime('now', 'localtime', ?)
  AND event IN ('rating_up','stock_out','back_in_stock','price_up','price_down')
GROUP BY norm_name
"""


# ═══════════════════════════════════════════════════════════════════
#  الطبقة النقيّة — تُختبر بلا قاعدة
# ═══════════════════════════════════════════════════════════════════

def raw_demand_score(row: dict[str, Any]) -> float:
    """الدرجة الخام الموزونة قبل التطبيع. خالصة."""
    return sum(w * float(row.get(key, 0) or 0) for key, _label, w, _src in COMPONENTS)


def tier_of(score: float) -> str:
    """طبقة العرض من الدرجة المطبَّعة. خالصة."""
    if score >= TIER_HIGH:
        return "مرتفع"
    if score >= TIER_MEDIUM:
        return "متوسط"
    return "منخفض"


def score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """يضيف ``demand_score`` (0-100) و``demand_tier`` لكل صفّ. خالصة.

    التطبيع **نسبيّ داخل النافذة** (إلى أعلى منتج) لا مطلق: المؤشر يقول «هذا
    مطلوب أكثر من ذاك»، ولا يدّعي أبداً كمّية مباعة. الدرجات السالبة (خفض سعر
    غالب) تُقصّ إلى صفر — «طلب منخفض» لا طلب سالب.
    """
    scored = [{**r, "_raw": raw_demand_score(r)} for r in rows]
    top = max((r["_raw"] for r in scored), default=0.0)
    out: list[dict[str, Any]] = []
    for row in scored:
        raw = row.pop("_raw")
        score = round(max(0.0, raw) / top * 100, 1) if top > 0 else 0.0
        out.append({**row, "demand_score": score, "demand_tier": tier_of(score)})
    out.sort(key=lambda r: r["demand_score"], reverse=True)
    return out


def explain(row: dict[str, Any]) -> list[dict[str, Any]]:
    """تفصيل المكوّنات للعرض: كل مكوّن بمصدره ونافذته الزمنية (شرط م7).

    لا يُعرض مؤشر بلا هذا التفصيل — رقمٌ مجرّد بلا مصدر يُقرأ كأنه مبيعات.
    """
    return [
        {
            "المكوّن": label,
            "العدد": int(row.get(key, 0) or 0),
            "الوزن": weight,
            "المصدر": source,
            "النافذة": f"آخر {WINDOW_DAYS} يوماً",
        }
        for key, label, weight, source in COMPONENTS
    ]


# ═══════════════════════════════════════════════════════════════════
#  طبقة القاعدة — قراءة فقط + جدول مشتق
# ═══════════════════════════════════════════════════════════════════

def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return str(db_path)
    from utils.data_paths import get_data_db_path

    return get_data_db_path("pricing_v18.db")


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(path).replace("\\", "/") + "?mode=ro", uri=True)


def compute_demand_index(db_path: Optional[str] = None) -> list[dict[str, Any]]:
    """يحسب المؤشر من مجرى الأحداث. قراءة فقط؛ فشلٌ ⇒ قائمة فارغة."""
    path = _db_path(db_path)
    try:
        con = _ro(path)
        try:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute(_AGG_SQL, (f"-{int(WINDOW_DAYS)} days",))]
        finally:
            con.close()
    except Exception as exc:
        logger.warning("تعذّر حساب مؤشر الطلب (يُتجاهل): %s", exc)
        return []
    return score_rows(rows)


def rebuild_demand_index(db_path: Optional[str] = None) -> int:
    """يعيد بناء الجدول المشتق ``product_demand_index`` ذرّياً. يعيد عدد الصفوف.

    كاش مشتق يجوز مسحه (ليس أرشيفاً) — DROP+CREATE+INSERT داخل ``BEGIN`` صريح
    (إلزامي على py3.14)؛ أي فشل ⇒ تراجع كامل فيبقى الكاش القديم سليماً.
    """
    rows = compute_demand_index(db_path)
    if not rows:
        return 0
    path = _db_path(db_path)
    try:
        con = sqlite3.connect(path)
        try:
            with con:
                con.execute("BEGIN")
                con.execute("DROP TABLE IF EXISTS product_demand_index")
                con.execute(
                    "CREATE TABLE product_demand_index ("
                    " norm_name TEXT PRIMARY KEY, store_count INTEGER,"
                    " rating_up INTEGER, stock_out INTEGER, back_in_stock INTEGER,"
                    " price_up INTEGER, price_down INTEGER, last_event TEXT,"
                    " demand_score REAL, demand_tier TEXT, window_days INTEGER,"
                    " rebuilt_at TEXT DEFAULT (datetime('now','localtime')))"
                )
                con.executemany(
                    "INSERT OR REPLACE INTO product_demand_index"
                    " (norm_name, store_count, rating_up, stock_out, back_in_stock,"
                    "  price_up, price_down, last_event, demand_score, demand_tier,"
                    "  window_days)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(
                        r["norm_name"], r.get("store_count", 0), r.get("rating_up", 0),
                        r.get("stock_out", 0), r.get("back_in_stock", 0),
                        r.get("price_up", 0), r.get("price_down", 0),
                        r.get("last_event", ""), r["demand_score"], r["demand_tier"],
                        WINDOW_DAYS,
                    ) for r in rows],
                )
            return len(rows)
        finally:
            con.close()
    except Exception as exc:
        logger.warning("إعادة بناء مؤشر الطلب فشلت (تجاهل): %s", exc)
        return 0


def demand_for(norm_names: Any, db_path: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """يقرأ المؤشر المحسوب لأسماء محدّدة. غياب الجدول ⇒ قاموس فارغ (لا كسر)."""
    names = [str(n) for n in (norm_names or []) if str(n or "").strip()]
    if not names:
        return {}
    path = _db_path(db_path)
    try:
        con = _ro(path)
        try:
            con.row_factory = sqlite3.Row
            out: dict[str, dict[str, Any]] = {}
            for chunk_start in range(0, len(names), 400):  # حدّ متغيّرات SQLite
                chunk = names[chunk_start:chunk_start + 400]
                marks = ",".join("?" * len(chunk))
                for r in con.execute(
                    f"SELECT * FROM product_demand_index WHERE norm_name IN ({marks})",
                    chunk,
                ):
                    row = dict(r)
                    out[str(row["norm_name"])] = row
            return out
        finally:
            con.close()
    except Exception:
        return {}
