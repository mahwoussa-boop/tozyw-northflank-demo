"""services/alert_service.py — تنبيه بأولوية: «نفد عند المنافس ومتوفر عندك».

الفكرة: أعلى إشارة بيع لحظية في السوق هي منافس نفد مخزونه من منتجٍ **نبيعه** —
طلبه سيبحث عن بديل. المصدر: ``product_signal_events`` (حدث ``stock_out`` الذي
يكتبه الكاشط) متقاطعاً مع ``our_catalog``.

درس أداء مقصود: التقاطع **مسحان بسيطان + set في بايثون** (~0.5s على 103k حدث)
لا ``EXISTS`` متداخلاً على LOWER/TRIM (يعلّق دقائق — لا فهرس على التعبير).

مبادئ: قراءة ``mode=ro`` فقط · أي خطأ ⇒ قائمة فارغة (لوحة لا تنكسر) ·
الطبقة النقية (``group_stockouts``) تُختبر بلا قاعدة.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

logger = logging.getLogger("AlertService")

# نافذة «العاجل» بالأيام — أقدم من هذا ليس تنبيهاً بل تاريخ.
HOT_WINDOW_DAYS = 7


def group_stockouts(
    hits: list[tuple], limit: int = 20,
) -> list[dict[str, Any]]:
    """يجمّع (منافس, اسم مطبّع, وقت) حسب المنتج ويرتّب بالأهمية (دالة خالصة).

    الأهمية = عدد المتاجر التي نفدت (نفادٌ في متاجر أكثر ⇒ طلب أوسع بلا معروض)
    ثم الأحدث وقوعاً. ``limit<=0`` ⇒ الكل.
    """
    groups: dict[str, dict[str, Any]] = {}
    for comp, nn, ts in hits:
        g = groups.setdefault(str(nn), {"stores": set(), "last_at": ""})
        g["stores"].add(str(comp))
        if str(ts) > g["last_at"]:
            g["last_at"] = str(ts)
    out = [
        {"name": name, "stores": sorted(g["stores"]),
         "n_stores": len(g["stores"]), "last_at": g["last_at"]}
        for name, g in groups.items()
    ]
    out.sort(key=lambda x: (x["n_stores"], x["last_at"]), reverse=True)
    return out[:limit] if limit and limit > 0 else out


# ── الملخّص اليومي غير العاجل (النصف الثاني من م5: «الباقي ملخّص يومي») ────────
# نافذة الملخّص بالساعات — يوم واحد: أقدم من هذا يظهر في ملخّص أمسه لا اليوم.
DIGEST_WINDOW_HOURS = 24


def rank_movers(hits: list[tuple], limit: int = 5) -> list[dict[str, Any]]:
    """يرتّب تحرّكات الأسعار ``(منافس, اسم, قديم, جديد, وقت)`` بأكبر نسبة تغيّر (دالة خالصة).

    لكل منتج يُحتفظ بأكبر تحرّك واحد (بالقيمة المطلقة)؛ سعر قديم/جديد ≤0 أو غير
    رقمي ⇒ يُهمل الصف (بيانات كشط فاسدة لا تدخل الملخّص). ``limit<=0`` ⇒ الكل.
    """
    best: dict[str, dict[str, Any]] = {}
    for comp, nn, old, new, ts in hits:
        try:
            old_f, new_f = float(old), float(new)
        except (TypeError, ValueError):
            continue
        if old_f <= 0 or new_f <= 0:
            continue
        pct = round((new_f - old_f) / old_f * 100.0, 1)
        cur = best.get(str(nn))
        if cur is None or abs(pct) > abs(cur["pct"]):
            best[str(nn)] = {
                "name": str(nn), "competitor": str(comp),
                "old": old_f, "new": new_f, "pct": pct, "at": str(ts),
            }
    out = sorted(best.values(), key=lambda x: abs(x["pct"]), reverse=True)
    return out[:limit] if limit and limit > 0 else out


def daily_digest(
    hours: int = DIGEST_WINDOW_HOURS,
    limit: int = 5,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """ملخّص السوق غير العاجل خلال ``hours`` ساعة: عدّادات الأحداث العامة +
    أكبر تحرّكات أسعار على منتجات **نبيعها** (تقاطع set كالنفاد — لا JOIN).

    الناتج ``{"counts": {حدث: عدد}, "movers": [...]}``؛ أي خطأ ⇒ ``{}`` — لا كسر للوحة.
    """
    try:
        if db_path is None:
            from utils.data_paths import get_data_db_path
            db_path = get_data_db_path("pricing_v18.db")
        ro = sqlite3.connect(
            "file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
        try:
            since = (f"-{int(hours)} hours",)
            counts = {
                str(ev): int(n)
                for ev, n in ro.execute(
                    "SELECT event, COUNT(*) FROM product_signal_events"
                    " WHERE run_at >= datetime('now', ?) GROUP BY event", since)
            }
            ours = {
                str(r[0]).strip().lower()
                for r in ro.execute(
                    "SELECT norm_name FROM our_catalog WHERE norm_name IS NOT NULL")
            }
            moves = ro.execute(
                "SELECT competitor, norm_name, old_val, new_val, run_at"
                " FROM product_signal_events"
                " WHERE event IN ('price_up','price_down')"
                " AND run_at >= datetime('now', ?)", since,
            ).fetchall()
        finally:
            ro.close()
        hits = [m for m in moves if str(m[1] or "").strip().lower() in ours]
        return {"counts": counts, "movers": rank_movers(hits, limit=limit)}
    except Exception as exc:
        logger.warning("تعذّر حساب ملخّص السوق اليومي (يُعاد فارغاً): %s", exc)
        return {}


def filter_still_out(events: list[tuple]) -> list[tuple]:
    """يبقي فقط أزواج (منافس، منتج) التي **لا تزال** نافدة الآن (دالة خالصة).

    ``events``: صفوف ``(منافس, اسم مطبّع, نوع الحدث, وقت)`` من ``stock_out``
    و``back_in_stock`` معاً. لكل زوج يُعتمَد **أحدث** حدث معروف له فقط: إن كان
    ``back_in_stock`` فالمنافس عاد للتوفر فعلاً — الفرصة انتهت، يُستبعد الزوج
    (الاتجاه المعاكس لسياق الخطة §ب2: «كشف إعادة التوفر» يقوّي يقين ما تبقّى
    بدل عرض تنبيهات نفاد انتهت صلاحيتها). التعادل الزمني الصارم يُفضّل الأحدث
    فرزاً نصياً (طابع ``YYYY-MM-DD HH:MM:SS`` قابل للمقارنة مباشرة).
    """
    latest: dict[tuple, tuple] = {}
    for comp, nn, ev, ts in events:
        key = (str(comp), str(nn))
        cur = latest.get(key)
        if cur is None or str(ts) >= cur[1]:
            latest[key] = (str(ev), str(ts))
    return [
        (comp, nn, ev_ts[1]) for (comp, nn), ev_ts in latest.items()
        if ev_ts[0] == "stock_out"
    ]


def urgent_stockouts(
    days: int = HOT_WINDOW_DAYS,
    limit: int = 20,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """منتجاتنا التي **لا تزال** نافدة عند منافسين خلال ``days`` يوماً، مرتّبة بالأهمية.

    ب2 (التوسعة): تُستبعد أزواج (منافس، منتج) التي عاد توفرها لاحقاً
    (``back_in_stock`` أحدث من آخر ``stock_out`` لها) — الفرصة انتهت فعلياً،
    عرضها كعاجلة تضليل. مسحان + تقاطع set (لا JOIN متداخلاً). أي خطأ ⇒ ``[]``.
    """
    try:
        if db_path is None:
            from utils.data_paths import get_data_db_path
            db_path = get_data_db_path("pricing_v18.db")
        ro = sqlite3.connect(
            "file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
        try:
            ours = {
                str(r[0]).strip().lower()
                for r in ro.execute(
                    "SELECT norm_name FROM our_catalog WHERE norm_name IS NOT NULL")
            }
            events = ro.execute(
                "SELECT competitor, norm_name, event, run_at FROM product_signal_events"
                " WHERE event IN ('stock_out', 'back_in_stock')"
                " AND run_at >= datetime('now', ?)",
                (f"-{int(days)} days",),
            ).fetchall()
        finally:
            ro.close()
        still_out = filter_still_out(events)
        hits = [
            (comp, nn, ts) for comp, nn, ts in still_out
            if str(nn or "").strip().lower() in ours
        ]
        return group_stockouts(hits, limit=limit)
    except Exception as exc:
        logger.warning("تعذّر حساب تنبيهات النفاد (تُعاد قائمة فارغة): %s", exc)
        return []
