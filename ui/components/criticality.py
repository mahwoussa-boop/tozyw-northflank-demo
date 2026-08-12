"""ui/components/criticality.py — تصنيف الأهمية القصوى (الحرجية) للبطاقات.

يحدّد درجة خطورة/أهمية كل منتج (🔴 حرج / 🟡 متوسط / 🟢 عادي) من إشارات متاحة فعلاً
في مخرجات المحرك، دون لمس المحرك ودون اختلاق بيانات:

1. عمود ``الخطورة`` الذي يحسبه المحرك أصلاً (مصدر الحقيقة الأساسي،
   #PRESERVED_LOGIC engine.py:2318-2325 = فجوة% × ثقة المطابقة).
2. فجوة سعر ضخمة بيننا وبين المنافس (من ``الفرق``/``سعر_المنافس``) — تُصعّد الدرجة.
3. **خطّاف نفاد مخزون المنافس** (عمود ``availability`` وما يماثله) — يُملأ لاحقاً من
   الكشط (المهمة 3)؛ غيابه الآن = لا أثر (no-op) فلا يكسر شيئاً.

كل الدوال نقية قابلة للاختبار بلا Streamlit. القاعدة: **نُصعّد فقط ولا نُخفّض** درجة
المحرك (شفافية: نضيف إشارات، لا نعارض المحرك). مستوى واحد = مصدر واحد
(``_level_and_reasons``) يغذّي الشارة والفلتر معاً (لا تباعد).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from conf.constants import COL_COMP_PRICE, COL_DIFF, COL_OUR_PRICE

# المستويات (متوافقة مع رموز المحرك 🔴/🟡/🟢 لتوحيد التعرّف البصري).
CRITICAL = "🔴 حرج"
MEDIUM = "🟡 متوسط"
NORMAL = "🟢 عادي"
_ALL = "الكل"
CRIT_CHOICES: list[str] = [_ALL, CRITICAL, MEDIUM, NORMAL]

# ألوان الشارة (نفس لوحة بطاقة الحَلَبة).
_COLORS: dict[str, str] = {CRITICAL: "#EF4444", MEDIUM: "#F59E0B", NORMAL: "#10B981"}
_RANK: dict[str, int] = {NORMAL: 0, MEDIUM: 1, CRITICAL: 2}

# عتبات فجوة السعر (% من سعر المنافس) — صريحة وقابلة للضبط.
HUGE_GAP_PCT = 25.0
MED_GAP_PCT = 12.0

_RISK_COL = "الخطورة"  # عمود عرض من المحرك (ليس في conf.constants).

# أعمدة توفّر المنافس — خطّاف يُملأ من الكشط لاحقاً (المهمة 3).
_AVAIL_COLS = (
    "availability", "توفر", "التوفر", "حالة_المخزون", "المخزون", "in_stock",
)
_OOS_TOKENS = (
    "out of stock", "out_of_stock", "outofstock", "sold out", "soldout",
    "unavailable", "غير متوفر", "غير متوفّر", "نفد", "نفذ", "نافد", "نفدت",
)


def _f(value: Any) -> float:
    try:
        if value is None or str(value).strip().lower() in ("", "nan", "none", "<na>"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _get(row: Any, key: str, default: Any = "") -> Any:
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    value = getter(key, default)
    return default if value is None or str(value) == "nan" else value


def gap_pct(row: Any) -> float:
    """فجوة السعر المطلقة % بيننا والمنافس (من ``الفرق`` أو سعرينا). خالص."""
    comp = _f(_get(row, COL_COMP_PRICE, 0))
    if comp <= 0:
        return 0.0
    diff = _f(_get(row, COL_DIFF, 0))
    if diff == 0:  # الفرق غير محسوب (مسار المفقودات) ⇒ احسبه من سعرينا
        our = _f(_get(row, COL_OUR_PRICE, 0))
        diff = (our - comp) if our > 0 else 0.0
    return abs(diff) / comp * 100.0


def competitor_out_of_stock(row: Any) -> bool:
    """خطّاف نفاد مخزون المنافس — يقرأ أعمدة التوفّر إن وُجدت (وإلا False). خالص."""
    for col in _AVAIL_COLS:
        raw = _get(row, col, "")
        if raw in ("", None):
            continue
        token = str(raw).strip().lower()
        if not token:
            continue
        # قيم منطقية صريحة: False/0 = غير متوفر.
        if token in ("false", "0", "no", "لا"):
            return True
        if any(tok in token for tok in _OOS_TOKENS):
            return True
    return False


def _level_and_reasons(row: Any) -> tuple[str, list[str]]:
    """المصدر الوحيد لحساب الدرجة + أسبابها (يغذّي الشارة والفلتر). خالص.

    نبدأ من خطورة المحرك ثم **نُصعّد فقط** بإشارات الفجوة/المخزون (لا نُخفّض).
    """
    reasons: list[str] = []
    risk = str(_get(row, _RISK_COL, "")).strip()
    if "حرج" in risk:
        level = CRITICAL
        reasons.append("خطورة المحرك: حرج")
    elif "متوسط" in risk:
        level = MEDIUM
        reasons.append("خطورة المحرك: متوسط")
    else:
        level = NORMAL

    gap = gap_pct(row)
    if gap >= HUGE_GAP_PCT:
        comp = _f(_get(row, COL_COMP_PRICE, 0))
        diff = _f(_get(row, COL_DIFF, 0))
        if diff == 0:
            diff = _f(_get(row, COL_OUR_PRICE, 0)) - comp
        direction = "منافس أرخص بكثير" if diff > 0 else "منافس أغلى بكثير"
        reasons.append(f"فجوة سعر ضخمة {gap:.0f}% ({direction})")
        level = _escalate(level, CRITICAL)
    elif gap >= MED_GAP_PCT:
        reasons.append(f"فجوة سعر {gap:.0f}%")
        level = _escalate(level, MEDIUM)

    if competitor_out_of_stock(row):
        reasons.append("نفد مخزون المنافس (فرصة)")
        level = _escalate(level, CRITICAL)

    return level, reasons


def _escalate(current: str, candidate: str) -> str:
    """يعيد الأعلى رتبةً (تصعيد فقط)."""
    return candidate if _RANK[candidate] > _RANK[current] else current


def criticality_of_row(row: Any) -> str:
    """درجة الحرجية لصف واحد (🔴/🟡/🟢). خالص."""
    return _level_and_reasons(row)[0]


def criticality_reasons(row: Any) -> list[str]:
    """أسباب الدرجة (للتلميح/الشفافية). خالص."""
    return _level_and_reasons(row)[1]


def color_for(level: str) -> str:
    """لون الشارة للدرجة."""
    return _COLORS.get(level, _COLORS[NORMAL])


def row_criticality_badge(row: Any) -> tuple[str, str, str]:
    """يعيد (التسمية، اللون، التلميح) لشارة البطاقة. خالص.

    التلميح = أسباب التصعيد مفصولة بـ « · » (يظهر عند المرور بالماوس على الشارة).
    """
    level, reasons = _level_and_reasons(row)
    return level, color_for(level), " · ".join(reasons)


def criticality_series(df: pd.DataFrame) -> pd.Series:
    """عمود درجات الحرجية لكل صف (يغذّي الفلتر/العدّاد). خالص وآمن."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.Series([], dtype=object)
    return df.apply(criticality_of_row, axis=1)


def filter_by_criticality(df: pd.DataFrame, choice: str) -> pd.DataFrame:
    """يفلتر DataFrame حسب درجة الحرجية المختارة («الكل» = بلا فلترة). خالص."""
    if not isinstance(df, pd.DataFrame) or df.empty or choice == _ALL or not choice:
        return df
    levels = criticality_series(df)
    return df[levels == choice]


def criticality_counts(df: pd.DataFrame) -> dict[str, int]:
    """عدّاد {حرج, متوسط, عادي} للقسم (للعرض الشفّاف). خالص."""
    base = {CRITICAL: 0, MEDIUM: 0, NORMAL: 0}
    if not isinstance(df, pd.DataFrame) or df.empty:
        return base
    counts = criticality_series(df).value_counts().to_dict()
    for level in base:
        base[level] = int(counts.get(level, 0))
    return base


# ══════════════════════════════════════════════════════════════════════
#  ترتيب «الأهم أولاً» (طلب المالك): الأكثر منافسين أرخص منّا = الأكثر خسارة = أهمّ
# ══════════════════════════════════════════════════════════════════════
_PRIORITY_COL = "_أولوية_عرض"  # عمود فرز عابر (لا يمسّ بيانات المصدر).


def competitors_below_count(row: Any) -> int:
    """عدد المنافسين الذين سعرهم **أقل من سعرنا** (0 < سعر المنافس < سعرنا). خالص.

    إشارة «نخسر»: كلّما زاد عدد المنافسين الأرخص منّا زادت أهمية المنتج (سعرنا مرتفع
    نسبةً للسوق ⇒ فرصة/تهديد أكبر). آمنة تماماً: أي غياب سعر/تفاصيل ⇒ 0 (fail-open)
    فلا تُصعّد منتجاً بلا بيانات ولا تكسر القسم.
    """
    try:
        our = _f(_get(row, COL_OUR_PRICE, 0))
        if our <= 0:
            return 0
        from ui.components.product_card import parse_competitor_details  # كسول (تجنّب دورة)
        comps = parse_competitor_details(row)
        return sum(1 for c in comps if 0 < _f(c.get("price")) < our)
    except Exception:
        return 0


def priority_sorted(df: pd.DataFrame) -> pd.DataFrame:
    """يرتّب القسم «الأهم أولاً» بعدد المنافسين الأرخص منّا تنازلياً. خالص وآمن.

    **عرض فقط** — يعمل على نسخة (df.copy) فلا يمسّ بيانات المصدر، ويحافظ على الترتيب
    الأصلي عند التساوي (kind=stable). أي خطأ ⇒ يعيد df كما هو (لا يكسر القسم).
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    try:
        d = df.copy()
        d[_PRIORITY_COL] = d.apply(competitors_below_count, axis=1)
        d = d.sort_values(_PRIORITY_COL, ascending=False, kind="stable")
        return d.drop(columns=[_PRIORITY_COL]).reset_index(drop=True)
    except Exception:
        return df
