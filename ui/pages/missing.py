"""ui/pages/missing.py — مركز عمليات المنتجات المفقودة (Operations Center).

══════════════════════════════════════════════════════════════════════════
يحوّل صفحة المفقودات من شاشة عرض إلى منصة تحكم متكاملة:
  • الفرز الذكي: المنتجات المؤكدة (green) في القمة دائماً.
  • شريط إجراءات جماعية: تحديد → تصدير Excel/CSV أو إرسال Make.com.
  • بطاقة تفاعلية: checkbox + بطاقة بصرية + تعديل السعر + أزرار فردية.
  • ربط بالمحرك: MissingProductsOrchestrator يعالج الدفعة بالكامل.

الدوال الخالصة (split_missing, confidence_counts, filter_by_confidence,
active_missing) تبقى كما هي — قابلة للاختبار بلا Streamlit.
══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from conf.constants import COL_COMP_LINK, COL_COMP_NAME
from services.decision_journal import record_decision
from ui.components.comparison_card import render_missing_card
from ui.components.page_header import render_page_header, section_accent
from ui.components.pagination import paginate, render_pagination
from ui.components.review_panel import render_review_ai_panel
from ui.components.salla_export import render_salla_export
from ui.components.card_actions import render_ai_verify
from ui.components.action_bar import render_action_bar
from core.enums import ActionType
from ui.state_manager import AppState, row_image_url

_CONF_COL = "مستوى_الثقة"
_LEVEL_GREEN = "green"     # مؤكد مفقود
_LEVEL_REVIEW = "review"   # محتمل (تحت المراجعة)
_CONF_CHOICES = ("الكل", "مؤكد", "محتمل", "مشكوك")

# نوع الفجوة — يقرأ «سبب_النقل» الذي يكتبه المحرّك (كان مكتوباً ولا يُعرَض).
_REASON_COL = "سبب_النقل"
_GAP_ALL = "الكل"
_GAP_NEW = "🆕 منتج جديد"
_GAP_SIZE = "📏 نسخة حجم نملكها"
_GAP_GENDER = "⚧ نسخة جنس نملكها"
_GAP_CHOICES = (_GAP_ALL, _GAP_NEW, _GAP_SIZE, _GAP_GENDER)

# روابط غير صالحة: لا يُعتدّ بها كرابط «مُعالَج» (str(NaN)=="nan" لرابط مفقود).
_JUNK_LINKS = frozenset({"", "nan", "none", "null", "na", "0", "-"})

# بادئة مفتاح اسم مستقر للمفقودات بلا رابط منافس (COL_COMP_LINK فارغ): تُعلَّم
# بـ «name::الاسم» فتُطابَق في active_missing وتختفي بعد الإرسال كأي مفقود.
_NAME_KEY_PREFIX = "name::"


def missing_row_key(row: Any) -> str:
    """مفتاح مُعالجة مستقر لصف مفقود: الرابط الصالح، وإلا «name::اسم_المنافس». خالص.

    يضمن أن منتجاً بلا رابط منافس صالح (COL_COMP_LINK فارغ/«nan») يُعلَّم بمفتاح
    ثابت من الاسم — فلا يبقى عالقاً في المفقودات بعد إرساله (يطابقه active_missing).
    يعيد "" إن غاب الرابط والاسم معاً (لا شيء مستقر نُعلّق عليه).
    """
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: None)
    link = str(getter(COL_COMP_LINK, "") or "").strip()
    if link.lower() not in _JUNK_LINKS:
        return link
    name = str(getter(COL_COMP_NAME, "") or "").strip()
    if name and name.lower() not in _JUNK_LINKS:
        return f"{_NAME_KEY_PREFIX}{name}"
    return ""


# ══════════════════════════════════════════════════════════════════════
#  دوال خالصة (Pure Functions — قابلة للاختبار بلا Streamlit)
# ══════════════════════════════════════════════════════════════════════
def split_missing(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """يفصل (مؤكد مفقود green، تحت المراجعة review). خالص وقابل للاختبار."""
    if df is None or df.empty or _CONF_COL not in df.columns:
        empty = pd.DataFrame()
        return (df if isinstance(df, pd.DataFrame) else empty), empty
    level = df[_CONF_COL].astype(str)
    return df[level == _LEVEL_GREEN], df[level == _LEVEL_REVIEW]


def confidence_counts(df: pd.DataFrame) -> tuple[int, int, int]:
    """عدّ (مؤكد green، محتمل review، مشكوك = أي قيمة أخرى). خالص."""
    if df is None or df.empty or _CONF_COL not in df.columns:
        return 0, 0, 0
    level = df[_CONF_COL].astype(str)
    confirmed = int((level == _LEVEL_GREEN).sum())
    potential = int((level == _LEVEL_REVIEW).sum())
    doubtful = int(len(df) - confirmed - potential)
    return confirmed, potential, doubtful


def filter_by_confidence(df: pd.DataFrame, choice: str = "الكل") -> pd.DataFrame:
    """يفلتر المفقودات بمستوى الثقة المختار (خالص، آمن من غياب العمود)."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    if choice == "الكل" or _CONF_COL not in df.columns:
        return df
    level = df[_CONF_COL].astype(str)
    if choice == "مؤكد":
        return df[level == _LEVEL_GREEN]
    if choice == "محتمل":
        return df[level == _LEVEL_REVIEW]
    if choice == "مشكوك":
        return df[~level.isin([_LEVEL_GREEN, _LEVEL_REVIEW])]
    return df


def gap_type(row: Any) -> str:
    """نوع الفجوة لصفّ مفقود: ``new`` / ``size`` / ``gender``. خالص وآمن.

    يقرأ ``سبب_النقل`` الذي يكتبه المحرّك وقت التحليل — بيانات موجودة أصلاً
    ولم تكن تُعرَض. تدقيق 2026-07-26: من 25,979 «مؤكد مفقود» هناك **5,129
    (19.7%) «نسخة حجم غير متوفرة لدينا — مطابقة 100%»** + 1,403 نسخ حجم
    بمطابقة 83–88% + 705 «نسخة جنس مختلفة». أي أن ~رُبع «المؤكد» ليس منتجاً
    جديداً بل حجماً/جنساً آخر لما نملكه — ولم يكن هناك ما يفرّق بينهما للمالك.
    """
    reason = ""
    get = getattr(row, "get", None)
    if callable(get):
        reason = str(get(_REASON_COL, "") or "")
    if "نسخة حجم" in reason:
        return "size"
    if "نسخة جنس" in reason:
        return "gender"
    return "new"


def gap_counts(df: pd.DataFrame) -> tuple[int, int, int]:
    """عدّ (جديد، نسخة حجم، نسخة جنس). خالص وآمن من غياب العمود."""
    if not isinstance(df, pd.DataFrame) or df.empty or _REASON_COL not in df.columns:
        n = 0 if not isinstance(df, pd.DataFrame) else len(df)
        return n, 0, 0
    reason = df[_REASON_COL].astype(str)
    size = int(reason.str.contains("نسخة حجم", na=False).sum())
    gender = int(reason.str.contains("نسخة جنس", na=False).sum())
    return len(df) - size - gender, size, gender


def filter_by_gap(df: pd.DataFrame, choice: str = "الكل") -> pd.DataFrame:
    """يفلتر بنوع الفجوة. ``الكل`` أو غياب العمود ⇒ لا تغيير. خالص."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    if choice == _GAP_ALL or _REASON_COL not in df.columns:
        return df
    reason = df[_REASON_COL].astype(str)
    is_size = reason.str.contains("نسخة حجم", na=False)
    is_gender = reason.str.contains("نسخة جنس", na=False)
    if choice == _GAP_NEW:
        return df[~(is_size | is_gender)]
    if choice == _GAP_SIZE:
        return df[is_size]
    if choice == _GAP_GENDER:
        return df[is_gender]
    return df


def active_missing(df: pd.DataFrame, state: AppState) -> pd.DataFrame:
    """يُسقط المفقودات المُرسَلة (الرابط ∈ processed) أو المخفاة (اسم المنافس). خالص.

    ⇒ تختفي من قائمة المفقودات وتظهر في «تمت المعالجة» (صفحات مرتّبة).
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    drop = pd.Series(False, index=df.index)
    # حماية: تجاهل الروابط غير الصالحة على الجانبين. str(NaN)=="nan" لرابط
    # مفقود؛ بدون هذا التصفية يطابق رابطٌ ملوّث ("nan") كلَّ صفّ بلا رابط
    # فتختفي كل المفقودات. (#bug: 24K مفقود تختفي بسبب "nan" في processed).
    valid_proc = {
        str(u).strip() for u in state.processed_missing_urls
        if str(u).strip().lower() not in _JUNK_LINKS
    }
    if valid_proc and COL_COMP_LINK in df.columns:
        links = df[COL_COMP_LINK].astype(str).str.strip()
        valid_link = ~links.str.lower().isin(_JUNK_LINKS)
        drop = drop | (links.isin(valid_proc) & valid_link)
    if valid_proc and COL_COMP_NAME in df.columns:
        # المفقودات بلا رابط صالح تُعلَّم بمفتاح اسم «name::الاسم» — نُسقطها أيضاً
        # كي تنتقل إلى «تمت المعالجة» بدل البقاء عالقةً في المفقودات.
        names = df[COL_COMP_NAME].astype(str).str.strip()
        name_keys = _NAME_KEY_PREFIX + names
        valid_name = (names != "") & ~names.str.lower().isin(_JUNK_LINKS)
        drop = drop | (name_keys.isin(valid_proc) & valid_name)
    if COL_COMP_NAME in df.columns:
        drop = drop | df[COL_COMP_NAME].astype(str).map(state.is_hidden)
    return df[~drop]


# ── طبقة أولوية «للقراءة فقط»: الحضور (عدد المنافسين) كإشارة استحقاق للإضافة ──
_PRESENCE_COL = "_حضور_المنافسين"   # عمود عرض/فرز عابر (لا يمسّ بيانات المصدر)
_presence_cache: dict = {}
_presence_loaded = False


def _competitor_presence(force: bool = False) -> dict:
    """خريطة ``{norm_name: عدد المنافسين المميّزين}`` من competitor_products_store.

    دفعة واحدة مُخبّأة في الذاكرة (لا استعلام لكل صف). آمنة تماماً: تُرجِع ``{}``
    عند أي خطأ (قاعدة غائبة/مقفلة) فلا تكسر صفحة المفقودات.
    """
    global _presence_loaded
    if _presence_loaded and not force:
        return _presence_cache
    try:
        import sqlite3
        import utils.db_manager as _dbm
        # قراءة تحليلية فقط ⇒ mode=ro (لا تأخذ قفل كتابة على القاعدة الحيّة أثناء العرض).
        conn = sqlite3.connect(
            "file:" + str(_dbm.DB_PATH).replace("\\", "/") + "?mode=ro", uri=True)
        try:
            # COUNT(*) == COUNT(DISTINCT competitor) لأن UNIQUE(competitor, norm_name)
            # يمنع تكرار المنافس لنفس norm_name — لكنه أسرع ~26× (يستخدم فهرس norm_name).
            rows = conn.execute(
                "SELECT norm_name, COUNT(*) "
                "FROM competitor_products_store GROUP BY norm_name"
            ).fetchall()
        finally:
            conn.close()
        _presence_cache.clear()
        _presence_cache.update({r[0]: int(r[1]) for r in rows if r[0]})
        _presence_loaded = True
    except Exception:
        return {}
    return _presence_cache


def _presence_badge(n: Any) -> str:
    """نص شارة الأولوية (عرض فقط). يظهر فقط حين الحضور ≥2 (إشارة تمايز حقيقية)."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    return f"⚡ موجود عند {n} منافسين" if n >= 2 else ""


_FIRST_SEEN_COL = "تاريخ_أول_رصد"
_NEW_WINDOW_DAYS = 7

_topcomp_cache: dict = {}
_topcomp_loaded = False


def _top_competitors(force: bool = False) -> dict:
    """خريطة ``{norm_name: {competitor, rating_count, rating_avg}}`` (أكبر منافس).

    دفعة واحدة مُخبّأة في الذاكرة (قراءة مفهرسة ~75م.ث للكل). آمنة تماماً: أي
    خطأ/غياب كاش ⇒ ``{}`` فلا تُظهر وسماً ولا تكسر الصفحة.
    """
    global _topcomp_loaded
    if _topcomp_loaded and not force:
        return _topcomp_cache
    try:
        from services.top_competitor_service import top_competitor_map
        _topcomp_cache.clear()
        _topcomp_cache.update(top_competitor_map())
        _topcomp_loaded = True
    except Exception:
        return {}
    return _topcomp_cache


def _top_competitor_badge_for(row: Any) -> str:
    """وسم «⭐ الأقوى» لصف مفقود عبر norm_name المطبَّع (عرض فقط، فارغ آمن)."""
    if not hasattr(row, "get"):
        return ""
    pres = _top_competitors()
    if not pres:
        return ""
    try:
        from engines.mahally_scraper import MahallyScraper
        from services.top_competitor_service import top_competitor_badge
        key = MahallyScraper.normalize(str(row.get("منتج_المنافس", "")))
        return top_competitor_badge(pres.get(key))
    except Exception:
        return ""


def _days_since_seen(date_str: Any) -> Optional[int]:
    """عدد الأيام منذ أول رصد (None إن غاب التاريخ أو تعذّر تحليله). خالص وآمن."""
    s = str(date_str or "").strip()
    if not s:
        return None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(s[:19]) if len(s) >= 19 else datetime.fromisoformat(s[:10])
        return max((datetime.now() - dt).days, 0)
    except (ValueError, TypeError):
        return None


def _new_badge(row: Any) -> str:
    """شارة «🆕 جديد» إن رُصد المنتج أول مرة خلال آخر أسبوع (عرض فقط)."""
    if not hasattr(row, "get"):
        return ""
    days = _days_since_seen(row.get(_FIRST_SEEN_COL))
    if days is None or days > _NEW_WINDOW_DAYS:
        return ""
    return "🆕 جديد اليوم" if days == 0 else f"🆕 جديد — رُصد قبل {days} يوم"


def sort_newest(df: pd.DataFrame) -> pd.DataFrame:
    """فرز «الأحدث رصداً» تنازلياً على ``تاريخ_أول_رصد`` (ثقة تكسر التعادل).

    آمن تماماً: غياب العمود ⇒ يعيد الفرز الذكي المعتاد دون كسر.
    """
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    if _FIRST_SEEN_COL not in df.columns:
        return smart_sort(df)
    df = df.copy()
    # الأحدث أولاً: نص فارغ يُدفَع للأسفل (يُعامَل كأقدم شيء).
    df["_sort_seen"] = df[_FIRST_SEEN_COL].astype(str).replace("", "0")
    df = df.sort_values("_sort_seen", ascending=False, kind="stable")
    return df.drop(columns=["_sort_seen"]).reset_index(drop=True)


def sort_most_rated(df: pd.DataFrame) -> pd.DataFrame:
    """فرز «الأكثر طلباً» تنازلياً على إجمالي تقييمات المنتج بالسوق.

    يقرأ ``total_rating_count`` من كاش أكبر منافس عبر norm_name المطبَّع. آمن
    تماماً: غياب الكاش/العمود ⇒ يعيد الفرز الذكي دون كسر (لا إشارة ⇒ سلوك اليوم).
    """
    if df is None or df.empty or "منتج_المنافس" not in df.columns:
        return smart_sort(df) if isinstance(df, pd.DataFrame) else pd.DataFrame()
    pres = _top_competitors()
    if not pres:
        return smart_sort(df)
    try:
        from engines.mahally_scraper import MahallyScraper
        df = df.copy()
        keys = df["منتج_المنافس"].astype(str).map(MahallyScraper.normalize)
        df["_sort_rated"] = [
            int((pres.get(k) or {}).get("total_rating_count", 0)) for k in keys
        ]
        df = df.sort_values("_sort_rated", ascending=False, kind="stable")
        return df.drop(columns=["_sort_rated"]).reset_index(drop=True)
    except Exception:
        return smart_sort(df)


def smart_sort(df: pd.DataFrame, presence: Optional[dict] = None) -> pd.DataFrame:
    """فرز ذكي + طبقة أولوية «للقراءة فقط».

    المفتاح الأساسي للأولوية: **الحضور** (عدد المنافسين الذين يبيعون المنتج)
    تنازلياً — الأكثر حضوراً = الأكثر استحقاقاً للإضافة. ثم الثقة (green→review→
    أخرى)، ثم الأرخص. الحضور يُقرأ من competitor_products_store (عبر norm_name)
    مع ``عدد_المنافسين`` احتياطاً للصفوف غير المطابقة (كي لا يُدفَع منتجٌ للأسفل خطأً).

    آمنة تماماً: أي غياب/خطأ في مصدر الحضور ⇒ العودة للفرز القديم (ثقة ثم سعر)
    دون كسر. لا تُغيّر بيانات المصدر (df.copy) — الترتيب البصري فقط.
    """
    if df is None or df.empty or _CONF_COL not in df.columns:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    # ترتيب الثقة: green=0 (أولاً)، review=1، أخرى=2
    df = df.copy()
    conf = df[_CONF_COL].astype(str)
    df["_sort_conf"] = conf.map(
        lambda v: 0 if v == _LEVEL_GREEN else (1 if v == _LEVEL_REVIEW else 2)
    )
    price_col = "سعر_المنافس"
    has_price = price_col in df.columns
    if has_price:
        df["_sort_price"] = pd.to_numeric(df[price_col], errors="coerce").fillna(9e9)

    # ── طبقة الأولوية (قراءة فقط، مطوَّقة): الحضور من DB + احتياطي عدد_المنافسين ──
    has_presence = False
    try:
        pres = presence if presence is not None else _competitor_presence()
        if pres and "منتج_المنافس" in df.columns:
            from engines.mahally_scraper import MahallyScraper
            agg = pd.to_numeric(
                df.get("عدد_المنافسين", 0), errors="coerce",
            ).fillna(0).astype(int)
            keys = df["منتج_المنافس"].astype(str).map(MahallyScraper.normalize)
            df[_PRESENCE_COL] = [
                int(pres.get(k, 0) or a) for k, a in zip(keys, agg)
            ]
            has_presence = True
    except Exception:
        has_presence = False  # مصدر الحضور غائب/معطوب ⇒ الفرز القديم

    sort_cols = ([_PRESENCE_COL] if has_presence else []) + ["_sort_conf"]
    ascending = ([False] if has_presence else []) + [True]
    if has_price:
        sort_cols.append("_sort_price")
        ascending.append(True)
    df = df.sort_values(sort_cols, ascending=ascending, kind="stable")
    df = df.drop(columns=["_sort_conf"] + (["_sort_price"] if has_price else []))
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════
#  HTML شريط التحكم اللاصق + شريط الإجراءات الجماعية
# ══════════════════════════════════════════════════════════════════════
def _build_missing_ctrl(n_total: int, confirmed: int, potential: int,
                        doubtful: int) -> str:
    """يبني HTML شريط التحكم اللاصق لصفحة المفقودات (إحصائيات الثقة)."""
    accent = section_accent("missing")
    return (
        f'<div class="mhw-ctrl" style="--ac:{accent}">'
        f'<div class="st-row">'
        f'<span class="st-i">📊 <b>{n_total:,}</b> مفقود</span>'
        f'<span class="st-i">✅ <b>{confirmed:,}</b> مؤكد</span>'
        f'<span class="st-i">⚠️ <b>{potential:,}</b> محتمل</span>'
        f'<span class="st-i">❓ <b>{doubtful:,}</b> مشكوك</span>'
        f'</div></div>'
    )


def _build_bulk_bar_css() -> str:
    """أنماط شريط الإجراءات الجماعية (يُحقن مرّة واحدة)."""
    return """<style>
.mhw-bulk-bar{background:linear-gradient(135deg,#1a1a2e,#16213e);
  border:1px solid #334155;border-radius:14px;padding:14px 18px;
  margin:8px 0 14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  box-shadow:0 4px 16px rgba(0,0,0,.3)}
.mhw-bulk-count{color:#E2E8F0;font-size:1.1rem;font-weight:700;
  display:flex;align-items:center;gap:8px}
.mhw-bulk-count .num{color:#818CF8;font-size:1.4rem;font-weight:900}
.mhw-bulk-divider{width:1px;height:32px;background:#334155;flex-shrink:0}
.mhw-card-interactive{display:flex;align-items:stretch;gap:0;
  margin:8px 0;border-radius:14px;overflow:hidden}
.mhw-card-check{width:48px;display:flex;align-items:center;justify-content:center;
  background:#0f172a;border:1px solid #1e293b;border-left:none;flex-shrink:0}
.mhw-card-body{flex:1;min-width:0}
.mhw-card-controls{width:220px;display:flex;flex-direction:column;gap:6px;
  padding:12px;background:#0f172a;border:1px solid #1e293b;border-right:none;
  justify-content:center;flex-shrink:0}
@media(max-width:768px){
  .mhw-card-interactive{flex-direction:column}
  .mhw-card-check{width:100%;height:36px;border:1px solid #1e293b}
  .mhw-card-controls{width:100%;border:1px solid #1e293b}
}
</style>"""


# ══════════════════════════════════════════════════════════════════════
#  عرض المراجعة (AI)
# ══════════════════════════════════════════════════════════════════════
def _render_review(review_df: pd.DataFrame, ai_service: Optional[Any]) -> None:
    """قسم المراجعة + زرّ تحقّق AI (لا حذف صامت عند فشل AI)."""
    import streamlit as st

    if review_df.empty:
        return
    st.warning(f"⚠️ {len(review_df)} منتجاً بحاجة تأكيد")
    if st.button("🤖 تحقّق AI من المراجعة", disabled=ai_service is None):
        st.session_state["_missing_ai_requested"] = True


# ══════════════════════════════════════════════════════════════════════
#  المكونات التفاعلية (الشريط العلوي + البطاقات)
# ══════════════════════════════════════════════════════════════════════
def _get_selected_keys(view_df: pd.DataFrame) -> list[int]:
    """يعيد فهارس المنتجات المحددة من session_state."""
    import streamlit as st
    selected = []
    for idx in view_df.index:
        if st.session_state.get(f"miss_sel_{idx}", False):
            selected.append(idx)
    return selected


def _render_bulk_action_bar(
    view_df: pd.DataFrame, state: AppState, export_service: Optional[Any],
) -> None:
    """شريط الإجراءات الجماعية: عداد + تصدير Excel + إرسال Make."""
    import streamlit as st

    selected = _get_selected_keys(view_df)
    n_selected = len(selected)

    # حقن CSS
    if not st.session_state.get("_mhw_bulk_css"):
        st.markdown(_build_bulk_bar_css(), unsafe_allow_html=True)
        st.session_state["_mhw_bulk_css"] = True

    # شريط HTML + أزرار Streamlit
    st.markdown(
        f'<div class="mhw-bulk-bar">'
        f'<div class="mhw-bulk-count">'
        f'<span class="num">{n_selected}</span> منتج محدد'
        f'</div>'
        f'<div class="mhw-bulk-divider"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    bc1, bc2, bc3 = st.columns([1, 1, 1])

    with bc1:
        if st.button(
            f"📦 تجهيز وتنزيل ملف سلة ({n_selected})",
            disabled=n_selected == 0,
            key="bulk_salla_btn",
            use_container_width=True,
        ):
            _bulk_export_salla(view_df, selected, state)

    with bc2:
        if st.button(
            f"🚀 إرسال الدفعة المحددة إلى Make ({n_selected})",
            disabled=n_selected == 0,
            key="bulk_make_btn",
            use_container_width=True,
        ):
            _bulk_send_make(view_df, selected, state)

    with bc3:
        if st.button(
            "☑️ تحديد/إلغاء الكل",
            key="bulk_toggle_all",
            use_container_width=True,
        ):
            # toggle: إذا كل الحالي محدد → ألغِ الكل، وإلا حدد الكل
            all_selected = all(
                st.session_state.get(f"miss_sel_{idx}", False)
                for idx in view_df.index
            )
            for idx in view_df.index:
                st.session_state[f"miss_sel_{idx}"] = not all_selected
            st.rerun()


def _bulk_export_salla(
    view_df: pd.DataFrame, selected: list[int], state: AppState,
) -> None:
    """تصدير المحددة إلى ملف سلة عبر المحرك التنفيذي."""
    import streamlit as st

    selected_df = view_df.loc[selected].copy()
    # تحديث الأسعار المعدلة يدوياً
    _apply_price_overrides(selected_df)

    with st.spinner("⏳ جاري تجهيز ملف سلة (المحرك التنفيذي يعمل)…"):
        try:
            from services.missing_orchestrator import MissingProductsOrchestrator
            orch = MissingProductsOrchestrator(
                our_catalog_df=getattr(state, "our_catalog", None),
                enrich_with_ai=False,  # سريع — بدون API
            )
            report = orch.process_dataframe(
                selected_df, export_target="salla",
            )
        except Exception as exc:
            st.error(f"❌ فشل التجهيز: {exc}")
            return

    if report.export_result.get("success") and report.export_result.get("csv_bytes"):
        csv_bytes = report.export_result["csv_bytes"]
        st.session_state["_bulk_salla_data"] = csv_bytes
        st.session_state["_bulk_salla_count"] = report.exported_count
        st.success(
            f"✅ جاهز: {report.exported_count} منتج | "
            f"مستبعد: {report.skipped_not_green + report.gate_rejected}"
        )
    else:
        errors = report.errors or [report.export_result.get("message", "خطأ غير معروف")]
        st.warning(f"⚠️ {errors[0]}")

    # عرض زر التنزيل إذا جاهز
    salla_data = st.session_state.get("_bulk_salla_data")
    if salla_data:
        st.download_button(
            f"⬇️ تنزيل ملف سلة ({st.session_state.get('_bulk_salla_count', 0)} منتج)",
            salla_data, "missing_salla_export.csv", mime="text/csv",
            key="bulk_salla_dl", use_container_width=True,
        )


def _bulk_send_make(
    view_df: pd.DataFrame, selected: list[int], state: AppState,
) -> None:
    """إرسال المحددة عبر Make.com عبر المحرك التنفيذي."""
    import streamlit as st

    selected_df = view_df.loc[selected].copy()
    _apply_price_overrides(selected_df)

    with st.spinner("🚀 جاري الإثراء (نوتات Fragrantica) والإرسال إلى Make.com…"):
        try:
            from services.missing_orchestrator import MissingProductsOrchestrator
            orch = MissingProductsOrchestrator(
                our_catalog_df=getattr(state, "our_catalog", None),
                enrich_with_ai=True,   # إرسالٌ للمتجر الحيّ ⇒ إثراءٌ إلزاميّ
                # (نوتات حقيقية + يعبر حارس جودة النوتات؛ بلا إثراء تُرفَض العطور).
            )
            report = orch.process_dataframe(
                selected_df, export_target="make",
            )
        except Exception as exc:
            st.error(f"❌ فشل الإرسال: {exc}")
            return

    res = report.export_result
    sent = int(res.get("sent", res.get("count", 0)) or 0)
    d = res.get("dedup", {})
    held = int(d.get("match", 0)) + int(d.get("likely", 0))
    if res.get("success") and sent > 0:
        note = f" · احتُجز {held} كمكرّر" if held else ""
        st.success(f"✅ أُرسل {sent} منتج إلى Make — تحقّق من سلة للتأكيد (200 = استلام لا إنشاء){note}")
        # تعليم المُرسلة كمعالَجة (بمفتاح مستقر: الرابط أو «name::الاسم» عند غيابه)
        for idx in selected:
            row = view_df.loc[idx]
            key = missing_row_key(row)
            if key:
                state.mark_missing_processed(key)
            # تسجيل صامت لكل منتج بالسعر المرسل فعلاً (دفعة)
            _nm = str(row.get(COL_COMP_NAME, "") if hasattr(row, "get") else "")
            try:
                _pr = float(selected_df.at[idx, "سعر_المنافس"])
            except (KeyError, TypeError, ValueError):
                _pr = None
            record_decision("send_new", _nm, "missing", chosen=_pr,
                            extra={"batch": True})
        state.persist_results()
    elif res.get("success") and sent == 0:
        # لم يُرسَل شيء فعلياً — احتجزت البوابة كل المحدد. لا نُعلّمه معالَجاً.
        st.warning(
            f"⚠️ لم يصل شيء — احتجزت بوابة منع التكرار كل المحدد "
            f"(مطابق {d.get('match', 0)} · محتمل {d.get('likely', 0)}). "
            f"راجِع «🛡️ محتمل مكرّر» أعلى الصفحة."
        )
    else:
        st.error(f"❌ {res.get('message', 'خطأ غير معروف')}")


def _apply_price_overrides(df: pd.DataFrame) -> None:
    """يطبّق الأسعار المعدلة يدوياً من session_state على DataFrame."""
    import streamlit as st
    for idx in df.index:
        override_key = f"miss_price_{idx}"
        if override_key in st.session_state:
            new_price = st.session_state[override_key]
            if new_price and float(new_price) > 0:
                df.at[idx, "سعر_المنافس"] = float(new_price)
                if "السعر_المقترح" in df.columns:
                    df.at[idx, "السعر_المقترح"] = float(new_price)


def _render_interactive_card(
    row: Any, idx: int, state: AppState,
    export_service: Optional[Any], ai_service: Optional[Any],
) -> None:
    """بطاقة تفاعلية: البطاقة البصرية في الأعلى، وشريط التحكم أسفلها."""
    import streamlit as st

    # ── صف 1: Checkbox + البطاقة البصرية (عرض كامل) ──
    col_check, col_card = st.columns([0.5, 9.5])

    with col_check:
        st.write("")  # محاذاة رأسية
        st.checkbox(
            "تحديد",
            key=f"miss_sel_{idx}",
            label_visibility="collapsed",
        )

    with col_card:
        render_missing_card(row)
        # شارة سبب الأولوية (عرض فقط): «⚡ موجود عند N منافسين» حين الحضور ≥2.
        _badge = _presence_badge(
            row.get(_PRESENCE_COL, 0) if hasattr(row, "get") else 0,
        )
        _fresh = _new_badge(row)
        if _badge or _fresh:
            st.caption(" · ".join(b for b in (_fresh, _badge) if b))
        # وسم «⭐ الأقوى»: أكثر منافس تقييماً على هذا المنتج (عرض فقط، فارغ آمن).
        _strong = _top_competitor_badge_for(row)
        if _strong:
            st.caption(_strong)
        # وسم تقييم المتجر (🏪 تحت اسم المتجر) — عرض فقط، فارغ آمن.
        try:
            from services.store_profile_service import store_badge_for_name
            _store = store_badge_for_name(
                str(row.get("المنافس", "")) if hasattr(row, "get") else "")
            if _store:
                st.caption(_store)
        except Exception:
            pass

    # ── صف 2: أدوات التحكم أسفل البطاقة (تخطيط محسّن) ──
    comp_price = 0.0
    try:
        comp_price = float(
            row.get("سعر_المنافس", 0) if hasattr(row, "get") else 0,
        )
    except (TypeError, ValueError):
        pass
    suggested = max(comp_price - 1, 0) if comp_price > 0 else 0

    c_price, c_send, c_verify, c_hide = st.columns([2.5, 3.5, 2.5, 1.5])

    with c_price:
        st.number_input(
            "💰 السعر (ر.س)",
            min_value=0.0,
            value=float(suggested),
            step=1.0,
            key=f"miss_price_{idx}",
            help="السعر المقترح = سعر المنافس − 1. عدّل حسب الحاجة.",
        )

    with c_send:
        key = missing_row_key(row)
        processed = bool(key) and key in state.processed_missing_urls

        if processed:
            st.success("✅ تم الإرسال")
        else:
            if st.button("🚀 تجهيز وإرسال Make", key=f"qs_{idx}",
                         type="primary", use_container_width=True):
                _enrich_and_send_single(row, idx, state, export_service)
            # ظهر فقط بعد احتجاز بوابة التكرار: إرسال متعمّد رغم التشابه.
            if st.session_state.get(f"_miss_held_{idx}") and st.button(
                "🚀 أرسله رغم التشابه", key=f"qsf_{idx}", use_container_width=True,
                help="يتخطّى بوابة منع التكرار — للنُّسخ المختلفة (تركيز/حجم)",
            ):
                _enrich_and_send_single(row, idx, state, export_service, force=True)

    with c_verify:
        render_ai_verify(row, ai_service, key=f"missing_{idx}")

    with c_hide:
        if st.button("❌ تجاهل", key=f"miss_hide_{idx}", use_container_width=True,
                      help="تجاهل / إخفاء هذا المنتج"):
            name = str(
                row.get(COL_COMP_NAME, "") if hasattr(row, "get") else "",
            )
            if name:
                state.hide(name)
                state.log_action(
                    key=name, name=name, action="تجاهل/إخفاء",
                    detail="أُزيل من المفقودات", kind="hidden",
                    image=row_image_url(row),
                )
                record_decision("ignore", name, "missing")
                state.persist_results()
                st.rerun()


def _enrich_and_send_single(
    row: Any, idx: int, state: AppState,
    export_service: Optional[Any], *, force: bool = False,
) -> None:
    """يُثري المنتج تلقائياً (كشط + وصف + ماركة) ثم يرسله إلى Make.com.

    ``force=True`` يتخطّى بوابة منع التكرار (إرسال «رغم التشابه» بقرار المستخدم).
    """
    import streamlit as st
    import os

    pname = str(row.get(COL_COMP_NAME, "") if hasattr(row, "get") else "")

    # قراءة السعر المعدّل يدوياً
    override_key = f"miss_price_{idx}"
    price_val = st.session_state.get(override_key)
    if price_val and float(price_val) > 0:
        price = float(price_val)
    else:
        try:
            price = float(row.get("سعر_المنافس", 0) if hasattr(row, "get") else 0)
        except (TypeError, ValueError):
            price = 0.0

    if price <= 0:
        st.warning("⚠️ السعر غير صالح — عدّل السعر أولاً")
        return

    webhook = os.environ.get("WEBHOOK_NEW_PRODUCTS", "")
    if not webhook:
        st.error("⚠️ رابط Webhook غير مهيّأ (.env)")
        return

    with st.spinner(f"⏳ تجهيز «{pname[:40]}» (إثراء + وصف + إرسال)…"):
        try:
            from services.missing_orchestrator import MissingProductsOrchestrator

            # تحضير DataFrame بصف واحد مع السعر المعدّل
            single_df = pd.DataFrame([row.to_dict() if hasattr(row, "to_dict") else dict(row)])
            single_df["سعر_المنافس"] = price
            if "السعر_المقترح" in single_df.columns:
                single_df["السعر_المقترح"] = price

            orch = MissingProductsOrchestrator(
                our_catalog_df=getattr(state, "our_catalog", None),
                enrich_with_ai=True,  # ← إثراء كامل (Fragrantica + وصف AI)
                skip_dedup=force,     # ← إرسال «رغم التشابه» عند طلب المستخدم
            )
            report = orch.process_dataframe(single_df, export_target="make")

        except Exception as exc:
            st.error(f"❌ فشل التجهيز: {exc}")
            return

    res = report.export_result
    sent = int(res.get("sent", res.get("count", 0)) or 0)
    if res.get("success") and sent > 0:
        st.success(f"✅ أُرسل «{pname[:40]}» إلى Make — تحقّق من سلة للتأكيد (200 = استلام لا إنشاء)")
        st.session_state.pop(f"_miss_held_{idx}", None)
        # تسجيل صامت: المقترَح (منافس−1) مقابل المرسَل فعلاً (لا يؤثّر على الإرسال)
        try:
            _cp = float(row.get("سعر_المنافس", 0) if hasattr(row, "get") else 0)
        except (TypeError, ValueError):
            _cp = 0.0
        record_decision("send_new", pname, "missing",
                        suggested=(round(_cp - 1) if _cp > 0 else None),
                        chosen=float(price))
        # تعليم كمعالَج (بمفتاح مستقر: الرابط أو «name::الاسم» عند غياب الرابط)
        key = missing_row_key(row)
        if key:
            state.mark_missing_processed(key)
            state.log_action(
                key=key, name=pname, action="تجهيز وإرسال (مفقود)",
                detail=f"إثراء تلقائي + إرسال بسعر {price:,.0f} ر.س ← Make",
                kind="missing", image=row_image_url(row),
            )
            state.persist_results()
        st.rerun()
    elif res.get("success") and sent == 0:
        # نجاح بلا إرسال فعلي = احتجزته بوابة منع التكرار. لا نُعلّمه معالَجاً، ونتيح
        # تجاوزها يدوياً («رغم التشابه») — لأن نسخة التركيز المختلفة منتج مختلف.
        d = res.get("dedup", {})
        st.session_state[f"_miss_held_{idx}"] = True
        st.warning(
            f"⚠️ لم يصل «{pname[:40]}» — احتجزته بوابة منع التكرار "
            f"(مطابق {d.get('match', 0)} · محتمل {d.get('likely', 0)}). إن كان "
            f"منتجاً مختلفاً فعلاً (تركيز/حجم) فاضغط «🚀 أرسله رغم التشابه» أدناه."
        )
    else:
        errors = report.errors or []
        msg = res.get("message", "خطأ غير معروف")
        st.error(f"❌ {errors[0] if errors else msg}")



# ══════════════════════════════════════════════════════════════════════
#  نقطة الدخول الرئيسية
# ══════════════════════════════════════════════════════════════════════
def _render_catalog_refresh(state: AppState) -> None:
    """زرّ «🔄 تحديث الكتالوج» — يسحب منتجات المتجر الحيّة من سلة (force) ويُسنِدها
    لـ ``state.our_catalog`` فيُطرّي فهرس منع التكرار. آمن: بلا توكن يعرض تلميحاً.
    """
    import streamlit as st

    try:
        from utils.salla_api import is_configured
        configured = is_configured()
    except Exception:
        configured = False

    if not configured:
        st.caption("⚠️ ضع `SALLA_ACCESS_TOKEN` في `.env` لسحب كتالوج متجرك تلقائياً.")
        return

    st.caption("يسحب منتجات متجرك الحيّة من سلة لتحديث فهرس منع التكرار (كاش ساعة).")
    if st.button("🔄 تحديث الكتالوج من المتجر", key="refresh_catalog",
                 use_container_width=True):
        with st.spinner("جاري سحب كتالوج المتجر من سلة…"):
            try:
                from utils.salla_api import get_store_catalog_df
                df = get_store_catalog_df(force=True)
            except Exception as exc:
                st.error(f"⚠️ تعذّر السحب: {exc}")
                return
        if df is not None and not df.empty:
            state.our_catalog = df
            st.success(f"✅ حُدّث الكتالوج: {len(df):,} منتج من متجرك")
        else:
            st.warning("لم تصل منتجات — تحقّق من التوكن والصلاحيات.")

    # ── مزامنة الماركات/التصنيفات بمعرّفاتها (لحلّ brand_id/category_id) ──
    st.caption("يزامن ماركات/تصنيفات متجرك بمعرّفاتها من سلة فتُحلّ "
               "`brand_id`/`category_id` في الحمولة.")
    if st.button("🔄 مزامنة الماركات/التصنيفات", key="sync_store_maps",
                 use_container_width=True):
        with st.spinner("جاري مزامنة الماركات والتصنيفات…"):
            try:
                from utils.salla_api import sync_store_maps
                res = sync_store_maps()
            except Exception as exc:
                st.error(f"⚠️ تعذّرت المزامنة: {exc}")
                return
        if res.get("brands") or res.get("categories"):
            st.success(f"✅ تمّت المزامنة: {res['brands']:,} ماركة · "
                       f"{res['categories']:,} تصنيف بمعرّفاتها")
        else:
            st.warning("لم تصل ماركات/تصنيفات — تحقّق من التوكن والصلاحيات.")


def _render_review_duplicates() -> None:
    """لوحة «محتمل مكرّر» — تُظهر ما حجبته بوابة منع التكرار كي لا يختفي جديد حقيقي.

    لكل عنصر: «جديد فعلاً» (⇒ ready_to_send) أو «مكرر» (⇒ يُزال). تظهر فقط عند
    وجود محجوزات. آمنة: أي خطأ لا يكسر الصفحة.
    """
    import streamlit as st

    try:
        from utils.missing_queue_manager import (
            get_review_duplicate_products,
            approve_review_duplicate,
            dismiss_review_duplicate,
        )
        items = get_review_duplicate_products()
    except Exception:
        return
    if not items:
        return

    with st.expander(f"🛡️ محتمل مكرّر — بانتظار مراجعتك ({len(items)})", expanded=False):
        st.caption(
            "احتجزت البوابة هذه المنتجات لتشابهها العالي مع كتالوجك (منعاً للتكرار) "
            "ولم تُرسَل. راجِعها: «جديد فعلاً» يُرسله، «مكرر» يُزيله."
        )
        for it in items[:50]:
            pkey   = str(it.get("product_key", "")).strip()
            name   = str(it.get("product_name", "")).strip()
            brand  = str(it.get("brand_name", "")).strip()
            reason = str(it.get("dup_reason", "")).strip()
            if not pkey:
                continue
            c1, c2, c3 = st.columns([6, 2, 2])
            label = f"**{name}**" + (f" — {brand}" if brand else "")
            if reason:
                label += (f"<br><span style='color:#8a8a8a;font-size:12px'>"
                          f"السبب: {reason}</span>")
            c1.markdown(label, unsafe_allow_html=True)
            if c2.button("✅ جديد فعلاً", key=f"rvd_ok_{pkey}", use_container_width=True):
                approve_review_duplicate([pkey])
                st.rerun()
            if c3.button("🗑️ مكرر", key=f"rvd_no_{pkey}", use_container_width=True):
                dismiss_review_duplicate([pkey])
                st.rerun()


_OWNED_CACHE_KEY = "_owned_matches"
_POSSIBLE_CACHE_KEY = "_possible_matches"
_OWNED_SIG_KEY = "_owned_matches_sig"
_OWNED_DISMISSED_KEY = "_owned_dismissed"


def _render_owned_elsewhere(missing_df: pd.DataFrame, state: AppState) -> None:
    """لوحة «قد تملكه باسم آخر» — تُصلح دقّة المفقودات دون فقدان بيانات.

    تفحص كل منتج مفقود بمطابقة دقيقة (حجم+تركيز+جنس+كلمات مميّزة متساوية) عبر
    ``services.ownership_matcher`` لتكشف ما نملكه فعلاً باسم مكتوب مختلف. لكل عنصر:
    «🗑️ أملكه» = إخفاء ناعم قابل للتراجع (لا حذف) فيخرج من المفقودات ويظهر في «تمت
    المعالجة»؛ «✅ جديد» = إبقاؤه (استبعاد من هذه القائمة فقط، جلسةً). الفحص خلف زر
    (ثقيل على كتالوج كبير) ومُخبّأ. آمنة تماماً: أي خطأ لا يكسر الصفحة.
    """
    import streamlit as st

    if not isinstance(missing_df, pd.DataFrame) or missing_df.empty:
        return
    our_df = getattr(state, "our_catalog", None)
    if our_df is None or (hasattr(our_df, "empty") and our_df.empty):
        return

    sig = (len(missing_df), len(our_df))
    with st.expander("🔁 قد تملكه باسم آخر — فحص دقيق للمكرّرات", expanded=False):
        st.caption(
            "يفحص كل منتج مفقود بدقّة (الحجم والتركيز والجنس والاسم) ليكشف ما تملكه "
            "فعلاً باسم مكتوب مختلف — فلا يظهر لك كمفقود خطأً. لا حذف: «أملكه» إخفاء "
            "قابل للتراجع من «تمت المعالجة»."
        )
        run = st.button("🔍 ابدأ الفحص الدقيق", key="owned_scan",
                        use_container_width=True)
        if not run and st.session_state.get(_OWNED_SIG_KEY) != sig:
            return  # لم يُطلب الفحص بعد (لا نُثقل كل تحميل صفحة)

        if st.session_state.get(_OWNED_SIG_KEY) != sig:
            with st.spinner("فحص دقيق للمطابقات… قد يستغرق دقيقة على كتالوج كبير"):
                from services.ownership_matcher import (
                    find_owned_matches, find_possible_matches,
                )
                st.session_state[_OWNED_CACHE_KEY] = find_owned_matches(missing_df, our_df)
                st.session_state[_POSSIBLE_CACHE_KEY] = find_possible_matches(missing_df, our_df)
                st.session_state[_OWNED_SIG_KEY] = sig
                st.session_state[_OWNED_DISMISSED_KEY] = set()

        dismissed = st.session_state.get(_OWNED_DISMISSED_KEY, set())
        matches = [
            m for m in st.session_state.get(_OWNED_CACHE_KEY, [])
            if not state.is_hidden(m.comp_name) and m.comp_name not in dismissed
        ]
        pmatches = [
            m for m in st.session_state.get(_POSSIBLE_CACHE_KEY, [])
            if not state.is_hidden(m.comp_name) and m.comp_name not in dismissed
        ]
        if not matches and not pmatches:
            st.success("✅ لا مكرّرات متبقّية — قسم المفقودات نظيف مما تملكه باسم آخر.")
            return

        if matches:
            st.markdown(f"**{len(matches)}** منتجاً مفقوداً يبدو أنك تملكه باسم آخر:")
            if st.button(f"🗑️ أملكها كلها — أزِل الـ{len(matches)} من المفقودات",
                         key="owned_hide_all", use_container_width=True):
                for m in matches:
                    state.hide(m.comp_name)
                    state.log_action(key=m.comp_name, name=m.comp_name,
                                     action="أملكه باسم آخر",
                                     detail=f"موجود في كتالوجك: {m.our_name}", kind="hidden")
                state.persist_results()
                st.rerun()

            for i, m in enumerate(matches[:100]):
                c1, c2, c3 = st.columns([6, 2, 2])
                c1.markdown(
                    f"**{m.comp_name}**<br><span style='color:#8a8a8a;font-size:12px'>"
                    f"عندك: {m.our_name}</span>", unsafe_allow_html=True,
                )
                if c2.button("🗑️ أملكه", key=f"own_hide_{i}", use_container_width=True):
                    state.hide(m.comp_name)
                    state.log_action(key=m.comp_name, name=m.comp_name,
                                     action="أملكه باسم آخر",
                                     detail=f"موجود في كتالوجك: {m.our_name}", kind="hidden")
                    state.persist_results()
                    st.rerun()
                if c3.button("✅ جديد", key=f"own_keep_{i}", use_container_width=True):
                    dismissed = set(st.session_state.get(_OWNED_DISMISSED_KEY, set()))
                    dismissed.add(m.comp_name)
                    st.session_state[_OWNED_DISMISSED_KEY] = dismissed
                    st.rerun()

        # طبقة «محتمل» — أقلّ ثقة، بلا زرّ جماعي، تُراجَع فرداً فرداً
        if pmatches:
            if matches:
                st.divider()
            st.markdown(
                f"🔸 **وربّما تملك هذه أيضاً** ({len(pmatches)}) — أقلّ تأكيداً "
                "(الحجم أو التركيز غير مذكور على أحد الطرفين). **راجِع كلّاً على حدة**:"
            )
            for i, m in enumerate(pmatches[:100]):
                c1, c2, c3 = st.columns([6, 2, 2])
                c1.markdown(
                    f"**{m.comp_name}**<br><span style='color:#8a8a8a;font-size:12px'>"
                    f"عندك: {m.our_name} — <i>{m.reason}</i></span>",
                    unsafe_allow_html=True,
                )
                if c2.button("🗑️ أملكه", key=f"poss_hide_{i}", use_container_width=True):
                    state.hide(m.comp_name)
                    state.log_action(key=m.comp_name, name=m.comp_name,
                                     action="أملكه باسم آخر (محتمل)",
                                     detail=f"موجود في كتالوجك: {m.our_name}", kind="hidden")
                    state.persist_results()
                    st.rerun()
                if c3.button("✅ جديد", key=f"poss_keep_{i}", use_container_width=True):
                    dismissed = set(st.session_state.get(_OWNED_DISMISSED_KEY, set()))
                    dismissed.add(m.comp_name)
                    st.session_state[_OWNED_DISMISSED_KEY] = dismissed
                    st.rerun()


def render(
    state: AppState,
    missing_df: pd.DataFrame,
    *,
    ai_service: Optional[Any] = None,
    export_service: Optional[Any] = None,
) -> None:
    """يعرض مركز عمليات المفقودات (شريط لاصق + إجراءات جماعية + بطاقات تفاعلية)."""
    import streamlit as st

    n_total = 0 if missing_df is None else len(missing_df)
    render_page_header("منتجات مفقودة", section="missing", count=n_total)
    if missing_df is None or missing_df.empty:
        if state.analysis_results is None:
            st.info("📋 لم تُجرِ تحليلاً بعد — اذهب إلى «📊 لوحة التحكم»، "
                    "ارفع كتالوجك، ثم اضغط «🚀 ابدأ التحليل».")
        else:
            st.info("لا منتجات مفقودة")
        return

    # ── شريط التحكم اللاصق (إحصائيات الثقة — زجاجي) ──
    confirmed, potential, doubtful = confidence_counts(missing_df)
    st.markdown(
        _build_missing_ctrl(n_total, confirmed, potential, doubtful),
        unsafe_allow_html=True,
    )

    # ── لوحة «محتمل مكرّر» (ما حجبته بوابة منع التكرار — يظهر للمراجعة) ──
    _render_review_duplicates()

    # ── لوحة «قد تملكه باسم آخر» (مطابقة دقيقة — دقّة المفقودات دون فقدان بيانات) ──
    _render_owned_elsewhere(missing_df, state)

    # ── صف الفلاتر المدمج (ثقة + ترتيب + تصدير + أدوات AI) ──
    fc1, fc_gap, fc_sort, fc2 = st.columns([2, 2, 2, 2])
    choice = fc1.selectbox(
        "🎯 مستوى الثقة", _CONF_CHOICES, key="missing_conf",
        index=1,  # افتراضي: مؤكد — يمنع ظهور غير المؤكدة
    )
    # نوع الفجوة: «مؤكد مفقود» كان يخلط منتجاً جديداً كلياً بنسخة حجم لما نملكه
    # (تدقيق 2026-07-26: 5,129 نسخة حجم بمطابقة 100% + 705 نسخة جنس داخل المؤكد).
    # الافتراضي «الكل» عمداً — لا نُخفي عنك شيئاً كان ظاهراً، بل نُتيح الفصل.
    _n_new, _n_size, _n_gender = gap_counts(missing_df)
    gap_choice = fc_gap.selectbox(
        "🧩 نوع الفجوة", _GAP_CHOICES, key="missing_gap", index=0,
        help=(f"🆕 جديد {_n_new:,} · 📏 نسخة حجم نملكها {_n_size:,} · "
              f"⚧ نسخة جنس {_n_gender:,} — المصدر عمود «سبب_النقل» من المحرّك."),
    )
    sort_mode = fc_sort.selectbox(
        "🔀 الترتيب", ("الأهم أولاً", "الأحدث رصداً", "🔥 الأكثر طلباً (تقييماً)"),
        key="missing_sort",
        index=0,  # افتراضي: الأهم (الأكثر حضوراً عند المنافسين)
    )

    # ── أدوات متقدمة في popover (AI + تصدير) ──
    with fc2.popover("🛠️ أدوات متقدمة"):
        # ── تحديث الكتالوج من المتجر (Salla API) — لتطرية فهرس منع التكرار ──
        st.markdown("##### 🔄 تحديث الكتالوج")
        _render_catalog_refresh(state)
        st.divider()

        # تصدير سلة
        st.markdown("##### 📥 تصدير سلة")
        st.caption(
            "يصدّر المفقودات المعروضة حسب فلتر الثقة إلى ملف CSV بقالب سلة "
            "(40 عمود + صفّ بيانات المنتج). فعّل «مؤكد» لتصدير المؤكَّدة فقط."
        )
        view_df_export = active_missing(
            filter_by_gap(filter_by_confidence(missing_df, choice), gap_choice), state,
        )
        render_salla_export(view_df_export, state.our_catalog)

    # ── لوحة AI (مطوية افتراضياً) ──
    _, review_df = split_missing(missing_df)
    _render_review(review_df, ai_service)
    from conf.constants import DATA_DIR
    render_review_ai_panel(missing_df, DATA_DIR, ai_service=ai_service)

    # ── فلترة + فرز (الأهم أولاً افتراضياً · الأحدث رصداً · الأكثر طلباً) ──
    _filtered = active_missing(
        filter_by_gap(filter_by_confidence(missing_df, choice), gap_choice), state,
    )
    if sort_mode == "الأحدث رصداً":
        view_df = sort_newest(_filtered)
    elif sort_mode.endswith("(تقييماً)"):
        view_df = sort_most_rated(_filtered)
    else:
        view_df = smart_sort(_filtered)

    if view_df.empty:
        st.info("لا منتجات مطابقة للفلتر المختار.")
        return

    # ── شريط الإجراءات الجماعية ──
    _render_bulk_action_bar(view_df, state, export_service)

    # عرض زر تنزيل سلة إذا جاهز (من عملية سابقة)
    salla_data = st.session_state.get("_bulk_salla_data")
    if salla_data:
        st.download_button(
            f"⬇️ تنزيل ملف سلة ({st.session_state.get('_bulk_salla_count', 0)} منتج)",
            salla_data, "missing_salla_export.csv", mime="text/csv",
            key="bulk_salla_dl_top", use_container_width=True,
        )

    # ── البطاقات التفاعلية ──
    page = int(st.session_state.get("missing_page", 1))
    view = paginate(view_df, page, per_page=12)
    st.caption(f"{view.caption} (من {len(missing_df)})")

    for _, (original_idx, row) in enumerate(view.items.iterrows()):
        _render_interactive_card(
            row, original_idx, state, export_service, ai_service,
        )
        st.write("")

    render_pagination(view, "missing")

    # ── شريط الإجراء العام (تجاهل الكل) ──
    action = render_action_bar(
        view.items, COL_COMP_NAME, "missing", section_df=view_df,
    )
    if action.action is ActionType.HIDE:
        for name in action.keys:
            state.hide(name)
            state.log_action(key=name, name=name, action="تجاهل/إخفاء",
                             detail="أُزيل من المفقودات", kind="hidden")
        state.persist_results()
        import streamlit as st
        st.rerun()
