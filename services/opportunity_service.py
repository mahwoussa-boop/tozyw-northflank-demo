"""
services/opportunity_service.py — «رادار الفرص» (قراءة فقط، إضافة صرفة)
======================================================================
خدمة تحليلية **للقراءة فقط** فوق ``competitor_products_store`` في
``pricing_v18.db``. تُجمّع منتجات المنافسين حسب الاسم المطبّع وتعطي كل
منتج «درجة أهمية» شفّافة، لكشف فرص الطلب/الندرة في السوق.

مبادئ صارمة (متوافقة مع قواعد الاشتباك):
  • لا آثار جانبية على القاعدة إطلاقاً — الاتصال يُفتح بوضع ``mode=ro``
    (لا أيّ عملية كتابة DML/DDL في هذا الملف).
  • مسار القاعدة من ``utils.data_paths.get_data_db_path`` لا مسار ثابت.
  • المنطق مقسوم طبقتين: جلب SQL (نجس بالـI/O) ودرجة/ترتيب **نقيّ**
    (``rank_opportunities`` دالة خالصة تُختبر بلا قاعدة).

الأعمدة المؤكَّدة (استطلاع 2026-07-05):
  competitor_products_store: competitor(TEXT=المتجر), norm_name, price(REAL),
  availability(INT 0=نفَذ/1=متوفر), rating_avg(REAL nullable), rating_count(INT).
  our_catalog: norm_name (لحقل العرض in_our_catalog فقط — خارج الدرجة).
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any, Optional

# ── أوزان الدرجة (ثوابت شفّافة قابلة للضبط — لا أرقام سحرية مدفونة) ──────────
#   الدرجة = W_STOCKOUT·نفاد + W_DEMAND·طلب + W_RATING·تقييم ، وكلها في [0,1]
#   فتكون الدرجة النهائية في [0,1] (مجموع الأوزان = 1).
W_STOCKOUT = 0.50   # الأعلى: نسبة المتاجر التي «نفَذ» لديها المنتج = فرصة بيع مباشرة
W_DEMAND   = 0.35   # الطلب: انتشار المنتج عبر المتاجر (كثرة البائعين = طلب مؤكَّد)
W_RATING   = 0.15   # الجودة: متوسط تقييم العملاء (يرجّح المنتجات المحبوبة)

# عتبة دنيا لعدد المتاجر — تستبعد ضجيج المنتج الموجود في متجر أو اثنين.
MIN_STORE_COUNT = 3

# مرجع تطبيع التقييم (سلّم التقييم 0..5).
RATING_SCALE = 5.0

# ── ثوابت السعر المقترَح (شفّافة قابلة للضبط — لا أرقام سحرية مدفونة) ──────────
UNDERCUT_SAR = 1.0   # نُنزل ريالاً واحداً تحت أرخص منافس *متوفّر* (اقتطاع بسيط لكسب السعر)
BAND_PCT = 0.20      # حاجز أمان: السعر المقترَح يبقى ضمن ±20% من وسيط السوق (يمنع الشذوذ)

# ── حارس الطزاجة: لا اقتراح سعر من بيانات كشط قديمة ──────────────────────────
#   بعد STALE_AFTER_DAYS يوماً بلا تحديث كشط تُعدّ بيانات المنافس قديمة، فيُحجب
#   السعر المقترَح فقط (طبقة فوق السعر لا داخله) — **لا يُخفى المنتج ولا تتأثّر
#   درجته/ترتيبه**. قابل للضبط.
STALE_AFTER_DAYS = 3


@dataclass
class Opportunity:
    """صفّ فرصة واحد — نتيجة تجميع اسم مطبّع عبر المتاجر."""
    norm_name: str
    store_count: int
    out_ratio: float                 # نسبة المتاجر بلا أي عرض متوفر ∈ [0,1]
    price_min: Optional[float]
    price_median: Optional[float]
    price_max: Optional[float]
    instock_price_min: Optional[float]   # أقل سعر لدى متجرٍ *متوفّر* — مرجع الاقتطاع
    rating_avg: Optional[float]      # متوسط التقييم (NULL متجاهَل في الحساب)
    rating_count: int
    in_our_catalog: bool             # عرض فقط — لا يدخل الدرجة
    suggested_price: Optional[float]     # السعر المقترَح (compute_suggested_price) — عرض فقط
    score: float = 0.0
    last_seen: Optional[str] = None       # MAX(updated_at) للمجموعة — يغذّي عمود «آخر تحديث»
    data_age_days: Optional[float] = None # قِدَم البيانات بالأيام (julianday الآن − آخر تحديث)
    stale_reason: Optional[str] = None    # "stale" إن حُجب الاقتراح للقِدَم، وإلا None
    image_url: Optional[str] = None       # عرض فقط: ممثّل صورة المنتج عند المنافسين
    size: Optional[str] = None            # عرض فقط: حجم/سعة العبوة إن توفّرت
    product_url: Optional[str] = None     # عرض فقط: رابط صفحة المنتج عند منافس

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── الطبقة النقيّة: الدرجة والترتيب (بلا قاعدة، قابلة للاختبار مباشرة) ────────
def score_row(row: dict[str, Any], max_store_count: int) -> float:
    """
    درجة أهمية شفّافة لصفّ واحد. كل إشارة مطبّعة إلى [0,1]:
      • نفاد  = out_ratio كما هو (نسبة أصلاً).
      • طلب   = store_count ÷ أعلى store_count في المجموعة (تطبيع نسبي متين).
      • تقييم = rating_avg ÷ 5 (NULL ⇒ 0، لا يُرجّح ولا يُعاقب سلبياً).
    ``in_our_catalog`` **غير** داخل في الدرجة عمداً (تفادي فخّ المفقودات المضخَّم).
    """
    stockout = row.get("out_ratio") or 0.0
    demand = (row["store_count"] / max_store_count) if max_store_count else 0.0
    rating = ((row.get("rating_avg") or 0.0) / RATING_SCALE)
    return W_STOCKOUT * stockout + W_DEMAND * demand + W_RATING * rating


def compute_suggested_price(
    instock_price_min: Optional[float],
    price_min: Optional[float],
    price_median: Optional[float],
    cost: Optional[float] = None,
) -> Optional[float]:
    """
    سعر مقترَح شفّاف: **أرخص بريال من أقل منافس متوفّر**، مضبوطاً داخل حاجز ±20%
    من وسيط السوق (يمنع الشذوذ)، مع أرضية تكلفة خامدة الآن (cost=0 دائماً).
    دالة خالصة (بلا SQL/قاعدة) — تُعيد ``None`` عند نقص البيانات.
    """
    # المرجع = أقل سعر لدى متجرٍ متوفّر؛ فإن لم يوجد (نفاد كامل) فأقل سعر إطلاقاً.
    ref = instock_price_min if (instock_price_min and instock_price_min > 0) else price_min
    # بلا مرجع صالح أو بلا وسيط صالح ⇒ بيانات غير كافية.
    if not ref or ref <= 0 or not price_median or price_median <= 0:
        return None
    raw = ref - UNDERCUT_SAR                    # الهدف: أقل بريال من أرخص متوفّر
    floor = price_median * (1 - BAND_PCT)       # لا تنزل تحت 80% من وسيط السوق
    ceiling = price_median * (1 + BAND_PCT)     # لا تصعد فوق 120% من وسيط السوق
    price = min(max(raw, floor), ceiling)       # اضبط ضمن الحاجز [floor, ceiling]
    if cost and cost > 0:                       # أرضية التكلفة (خامدة: cost=0 حالياً)
        price = max(price, cost)
    return round(price)


def apply_freshness_guard(
    suggested_price: Optional[float],
    data_age_days: Optional[float],
) -> tuple[Optional[float], Optional[str]]:
    """حارس طزاجة **فوق** السعر لا داخله: إن تجاوز قِدَم البيانات ``STALE_AFTER_DAYS``
    يُحجب السعر المقترَح (يُعاد ``None``) بسبب ``"stale"`` — كي لا نقترح سعراً من
    بيانات كشط قديمة. القِدَم غير المعروف (``None``) لا يُحجب (المخطط يضمن
    ``updated_at`` غير فارغ — صفر NULL في القاعدة الحيّة). دالة خالصة (بلا SQL).

    Returns: ``(السعر_أو_None, سبب_الحجب_أو_None)``.
    """
    if data_age_days is not None and data_age_days > STALE_AFTER_DAYS:
        return None, "stale"
    return suggested_price, None


def format_data_age(data_age_days: Optional[float]) -> str:
    """وصف بشري لقِدَم البيانات (عرض فقط): اليوم / أمس / قبل N يوم / غير معروف.
    دالة خالصة — القيم <1 (وحتى السالبة الطفيفة من فارق التوقيت) تُعدّ «اليوم»."""
    if data_age_days is None:
        return "غير معروف"
    if data_age_days < 1:
        return "اليوم"
    if data_age_days < 2:
        return "أمس"
    return f"قبل {int(data_age_days)} يوم"


def rank_opportunities(rows: list[dict[str, Any]]) -> list[Opportunity]:
    """
    دالة خالصة: تحوّل صفوف التجميع الخام إلى ``Opportunity`` مُدرَّجة،
    مرتّبة تنازلياً بالدرجة (كسر التعادل: store_count ثم out_ratio) — متينة
    أمام تغيّر البيانات لأنها تعتمد الترتيب النسبي لا قيماً مطلقة.
    """
    if not rows:
        return []
    max_store_count = max(int(r["store_count"]) for r in rows)
    out: list[Opportunity] = []
    for r in rows:
        instock_min = r.get("instock_price_min")
        # cost=None حالياً (cost_price كلها 0 في الكتالوج — أرضية التكلفة خامدة).
        _sugg = compute_suggested_price(
            instock_min, r.get("price_min"), r.get("price_median"), cost=None,
        )
        # حارس الطزاجة: يحجب الاقتراح إن قدُمت البيانات — لا يمسّ الدرجة/الترتيب.
        _age = r.get("data_age_days")
        _sugg, _stale = apply_freshness_guard(_sugg, _age)
        opp = Opportunity(
            norm_name=r["norm_name"],
            store_count=int(r["store_count"]),
            out_ratio=float(r.get("out_ratio") or 0.0),
            price_min=r.get("price_min"),
            price_median=r.get("price_median"),
            price_max=r.get("price_max"),
            instock_price_min=instock_min,
            rating_avg=r.get("rating_avg"),
            rating_count=int(r.get("rating_count") or 0),
            in_our_catalog=bool(r.get("in_our_catalog")),
            suggested_price=_sugg,
            last_seen=r.get("last_seen"),
            data_age_days=(float(_age) if _age is not None else None),
            stale_reason=_stale,
            image_url=r.get("image_url"),
            size=r.get("size"),
            product_url=r.get("product_url"),
            score=score_row(r, max_store_count),
        )
        out.append(opp)
    out.sort(key=lambda o: (o.score, o.store_count, o.out_ratio), reverse=True)
    return out


def opportunity_to_card_row(o: Opportunity) -> dict[str, Any]:
    """يحوّل فرصةً إلى صفّ أعمدة ``build_missing_card_html`` (عرض أمين، دالة نقيّة).

    تجميعٌ عبر متاجر ⇒ لا متجر/ماركة مفردة (فارغة آمنة). السعر = أقلّ سعرٍ *متوفّر*
    وإلا أدنى سعر. حقول قيمة الرادار (score/out_ratio/data_age_days) تُضاف كمفاتيح
    إضافية تتجاهلها البطاقة وتعرضها الخطوة 3 كشارات. بلا Streamlit ولا قاعدة.
    """
    price = o.instock_price_min if o.instock_price_min is not None else o.price_min
    return {
        # ── أعمدة البطاقة (missing_df) ──
        "منتج_المنافس": o.norm_name or "",
        "صورة_المنافس": o.image_url,            # قد تكون None — المكوّن يتحمّلها
        "سعر_المنافس": price,                    # instock وإلا price_min
        "السعر_المقترح": o.suggested_price,
        "رابط_المنافس": o.product_url or "",     # فارغ ⇒ المكوّن يولّد رابط بحث
        "المنافس": f"{o.store_count} متجر",       # لا متجر مفرد في التجميع
        "عدد_المنافسين": o.store_count,
        "الماركة": "",                           # لا ماركة مفردة
        "مستوى_الثقة": "",
        "منتج_مطابق_محتمل": "",
        "هو_تستر": "",
        "المنافسون": "",
        "ownership": "",
        # ── حقول قيمة الرادار (شارات الخطوة 3؛ تتجاهلها البطاقة) ──
        "score": o.score,
        "out_ratio": o.out_ratio,
        "data_age_days": o.data_age_days,
    }


# ── الطبقة النجسة: جلب التجميع بـSQL واحد كفؤ (mode=ro، بلا حلقات فوق 175k) ──
_AGG_SQL = """
WITH base AS (
    SELECT LOWER(TRIM(norm_name)) AS nn,
           competitor, price, availability, rating_avg, rating_count, updated_at,
           image_url, size, product_url
    FROM competitor_products_store
    WHERE norm_name IS NOT NULL AND TRIM(norm_name) <> ''
),
med AS (
    -- الوسيط عبر دوال النوافذ: متوسط القيمتين الوسطيتين (زوجي) أو الوسطى (فردي)
    SELECT nn, AVG(price) AS price_median
    FROM (
        SELECT nn, price,
               ROW_NUMBER() OVER (PARTITION BY nn ORDER BY price) AS rn,
               COUNT(*)     OVER (PARTITION BY nn)                AS cnt
        FROM base
        WHERE price > 0
    )
    WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
    GROUP BY nn
),
agg AS (
    SELECT nn,
           COUNT(DISTINCT competitor) AS store_count,
           COUNT(DISTINCT competitor)
               - COUNT(DISTINCT CASE WHEN availability = 1 THEN competitor END)
               AS out_stores,
           MIN(CASE WHEN price > 0 THEN price END) AS price_min,
           MAX(CASE WHEN price > 0 THEN price END) AS price_max,
           -- أقل سعر لدى متجرٍ متوفّر (availability=1) — مرجع الاقتطاع للسعر المقترَح
           MIN(CASE WHEN price > 0 AND availability = 1 THEN price END) AS instock_price_min,
           AVG(rating_avg)   AS rating_avg,   -- AVG يتجاهل NULL تلقائياً
           SUM(rating_count) AS rating_count,
           -- عرض فقط: ممثّل غير فارغ للصورة/الحجم/الرابط (تجميع بسيط، بلا استعلام مرتبط)
           MAX(NULLIF(image_url, ''))   AS image_url,
           MAX(NULLIF(size, ''))        AS size,
           MAX(NULLIF(product_url, '')) AS product_url,
           -- طزاجة البيانات: أحدث تحديث كشط للمجموعة + قِدَمه بالأيام (حارس الطزاجة)
           MAX(updated_at)   AS last_seen,
           julianday('now') - julianday(MAX(updated_at)) AS data_age_days
    FROM base
    GROUP BY nn
)
SELECT a.nn                                        AS norm_name,
       a.store_count                               AS store_count,
       CAST(a.out_stores AS REAL) / a.store_count  AS out_ratio,
       a.price_min                                 AS price_min,
       m.price_median                              AS price_median,
       a.price_max                                 AS price_max,
       a.instock_price_min                         AS instock_price_min,
       a.rating_avg                                AS rating_avg,
       a.rating_count                              AS rating_count,
       a.last_seen                                 AS last_seen,
       a.data_age_days                             AS data_age_days,
       a.image_url                                 AS image_url,
       a.size                                      AS size,
       a.product_url                               AS product_url,
       CASE WHEN EXISTS (
           SELECT 1 FROM our_catalog o
           WHERE LOWER(TRIM(o.norm_name)) = a.nn
       ) THEN 1 ELSE 0 END                         AS in_our_catalog
FROM agg a
LEFT JOIN med m ON m.nn = a.nn
WHERE a.store_count >= ?
"""


def _ro_connect(db_path: str) -> sqlite3.Connection:
    """اتصال قراءة-فقط صارم (mode=ro) — أي محاولة كتابة تفشل على مستوى SQLite."""
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_aggregates(db_path: str, min_stores: int) -> list[dict[str, Any]]:
    """يشغّل التجميع الوحيد على القاعدة (قراءة فقط) ويعيد صفوفاً كقواميس."""
    conn = _ro_connect(db_path)
    try:
        cur = conn.execute(_AGG_SQL, (min_stores,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def top_opportunities(
    limit: int = 25,
    min_stores: int = MIN_STORE_COUNT,
    db_path: Optional[str] = None,
) -> list[Opportunity]:
    """الواجهة العامة: تقرأ الفرص المُسبقة الحساب من ``opportunity_scores`` (سريع،
    mode=ro). الفلترة (min_stores/limit) والترتيب (score) وقت القراءة. جدول غائب/
    فارغ ⇒ ``[]`` (لا حساب حيّ — يتفادى بطء الـ39 ثانية؛ يُبنى الجدول وقت الكشط)."""
    if db_path is None:
        from utils.data_paths import get_data_db_path  # استيراد كسول — يبقي الوحدة خفيفة
        db_path = get_data_db_path("pricing_v18.db")
    sf = [f.name for f in fields(Opportunity)]
    sql = ("SELECT " + ", ".join(sf) + " FROM opportunity_scores "
           "WHERE store_count >= ? ORDER BY score DESC, store_count DESC, out_ratio DESC")
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    try:
        conn = _ro_connect(db_path)
        try:
            rows = conn.execute(sql, (min_stores,)).fetchall()
        finally:
            conn.close()
        return [Opportunity(**{c: r[c] for c in sf}) for r in rows]
    except Exception:
        return []


def latest_data_age_days(db_path: Optional[str] = None) -> Optional[float]:
    """قِدَم **أحدث** بيانات منافس عبر القاعدة (``global MAX(updated_at)``) بالأيام —
    لترويسة «أحدث بيانات المنافسين». قراءة فقط (``mode=ro``)؛ يعيد ``None`` عند
    غياب البيانات/الجدول (لا يكسر الصفحة)."""
    if db_path is None:
        from utils.data_paths import get_data_db_path
        db_path = get_data_db_path("pricing_v18.db")
    try:
        conn = _ro_connect(db_path)
        try:
            row = conn.execute(
                "SELECT julianday('now') - julianday(MAX(updated_at)) "
                "FROM competitor_products_store WHERE price > 0"
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception:
        return None


def rebuild_opportunity_scores(db_path: Optional[str] = None) -> int:
    """يعيد بناء opportunity_scores (كاش مشتق) وقت الكشط؛ DROP+CREATE+INSERT ذرّي؛ خطأ⇒0."""
    from utils.data_paths import get_data_db_path
    db_path = db_path or get_data_db_path("pricing_v18.db")
    sf = [f.name for f in fields(Opportunity)]
    cols, ph = ", ".join(sf + ["rebuilt_at"]), ", ".join(["?"] * (len(sf) + 1))
    try:
        ranked = rank_opportunities(_fetch_aggregates(db_path, 2))  # ≥2 متاجر: يخدم الرادار(≥3) والمفقودات(≥2)؛ يُسقط ~88% ضجيج المنتج المفرد ويجعل البناء عملياً
        ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(db_path)
        try:
            rows = [[getattr(o, c) for c in sf] + [ts] for o in ranked]
            # تبديل مخطّط ذرّي: DROP+CREATE+فهرسة+إدراج داخل معاملة واحدة صريحة (BEGIN)
            # يضمن الأعمدة الجديدة على جدول قائم؛ فشل ⇒ تراجع كامل فيبقى الكاش القديم سليماً.
            # BEGIN صريح ضروري: على py3.14 لا تنضمّ DDL للمعاملة ضمنياً فتُفقَد الذرّية.
            with conn:
                conn.execute("BEGIN")
                conn.execute("DROP TABLE IF EXISTS opportunity_scores")
                conn.execute(f"CREATE TABLE opportunity_scores ({cols})")
                conn.execute("CREATE INDEX idx_oppscore_nn ON opportunity_scores(norm_name)")
                conn.execute("CREATE INDEX idx_oppscore_score ON opportunity_scores(score)")
                conn.executemany(f"INSERT INTO opportunity_scores ({cols}) VALUES ({ph})", rows)
            return len(ranked)
        finally:
            conn.close()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("rebuild_opportunity_scores فشل: %s", exc)
        return 0
