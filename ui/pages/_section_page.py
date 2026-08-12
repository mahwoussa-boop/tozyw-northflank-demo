"""ui/pages/_section_page.py — العارض العام للأقسام (منطق مشترك رفيع).

يوحّد تدفّق: عنوان → شريط إحصائيات → فلاتر → تبديل بطاقات/جدول → ترقيم →
إجراءات جماعية، لأقسام سعر أعلى/أقل/موافق/مراجعة/مستبعد. لا منطق عمل —
الدوال الخالصة (إحصائيات/إخفاء/أعمدة الجدول) قابلة للاختبار بلا Streamlit.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from conf.constants import (
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_COMP_STORE,
    COL_DIFF,
    COL_MATCH_RATIO,
    COL_OUR_ID,
    COL_OUR_NAME,
    COL_OUR_PRICE,
)
from core.enums import ActionType, SectionType
from ui.components.action_bar import render_action_bar
from ui.components.comparison_card import render_row_arena
from ui.components.criticality import criticality_counts, filter_by_criticality
from ui.components.filter_bar import (
    apply_filters,
    filter_by_availability,
    render_filter_bar,
)
from ui.components.page_header import render_page_header, section_accent
from ui.components.pagination import paginate, render_pagination
from ui.components.price_action import render_price_action
from ui.components.status_badge import section_badge
from ui.state_manager import AppState

# الأقسام التي يظهر فيها إجراء تحديث السعر (لها منافس وقرار سعري واضح).
_PRICE_ACTION_SECTIONS: frozenset[SectionType] = frozenset({
    SectionType.PRICE_RAISE, SectionType.PRICE_LOWER, SectionType.APPROVED,
})

# أعمدة الجدول المختصر (تُعرض المتوفّر منها فقط).
_TABLE_COLS: tuple[str, ...] = (
    COL_OUR_NAME, COL_OUR_PRICE, COL_COMP_STORE,
    COL_COMP_NAME, COL_COMP_PRICE, COL_MATCH_RATIO, COL_DIFF,
)


def section_dataframe(
    sections: dict[str, pd.DataFrame], section: SectionType,
) -> pd.DataFrame:
    """يستخرج DataFrame القسم (المفتاح = قيمة التعداد). خالص وقابل للاختبار."""
    df = sections.get(section.value)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def section_stats(df: pd.DataFrame, diff_col: str = COL_DIFF) -> dict[str, float]:
    """إحصائيات سريعة من فروق الأسعار (خالص، آمن من NaN/غياب العمود)."""
    count = len(df) if isinstance(df, pd.DataFrame) else 0
    if not count or diff_col not in df.columns:
        return {"count": count, "avg": 0.0, "min": 0.0, "max": 0.0}
    diffs = pd.to_numeric(df[diff_col], errors="coerce").dropna()
    if diffs.empty:
        return {"count": count, "avg": 0.0, "min": 0.0, "max": 0.0}
    return {
        "count": count, "avg": float(diffs.mean()),
        "min": float(diffs.min()), "max": float(diffs.max()),
    }


def visible_dataframe(
    df: pd.DataFrame, state: AppState,
    name_col: str = COL_OUR_NAME, sku_col: str = COL_OUR_ID,
) -> tuple[pd.DataFrame, int]:
    """يستبعد المخفيّ والمُعالَج سعرياً ويعيد (المرئي، عدد المُزال). خالص.

    المنتجات التي حُدِّث سعرها (Make) أو أُخفيت تغادر القسم وتظهر في «تمت المعالجة»
    ⇒ تبقى صفحات الأقسام مرتّبة.
    """
    if not isinstance(df, pd.DataFrame) or df.empty or name_col not in df.columns:
        return (df if isinstance(df, pd.DataFrame) else pd.DataFrame()), 0
    drop_mask = df[name_col].astype(str).map(state.is_hidden)
    if sku_col in df.columns:
        drop_mask = drop_mask | df[sku_col].astype(str).map(state.is_price_processed)
    return df[~drop_mask], int(drop_mask.sum())


def transparency_counts(
    df: pd.DataFrame, state: AppState, *,
    name_col: str = COL_OUR_NAME, sku_col: str = COL_OUR_ID,
) -> dict[str, int]:
    """عدّاد شفافية القسم: يوجد/معروض/مخفي/معالَج. خالص وآمن من NaN/غياب العمود.

    - يوجد: إجمالي صفوف القسم.
    - مخفي: ما أخفاه المستخدم ناعماً (``state.is_hidden`` على ``name_col``).
    - معروض: يوجد − مخفي.
    - معالَج: ما حُدِّث سعره (``state.is_price_processed`` على ``sku_col``).
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {"exists": 0, "shown": 0, "hidden": 0, "processed": 0}
    exists = len(df)
    hidden_mask = (
        df[name_col].astype(str).map(state.is_hidden)
        if name_col in df.columns else pd.Series(False, index=df.index)
    )
    processed_mask = (
        df[sku_col].astype(str).map(state.is_price_processed)
        if sku_col in df.columns else pd.Series(False, index=df.index)
    )
    hidden = int(hidden_mask.sum())
    processed = int(processed_mask.sum())
    # «معروض» = ليس مخفيّاً ولا معالَجاً (دقيق عند تداخل الحالتين).
    shown = int((~(hidden_mask | processed_mask)).sum())
    return {"exists": exists, "shown": shown, "hidden": hidden, "processed": processed}


def table_columns(df: pd.DataFrame) -> list[str]:
    """أعمدة الجدول المختصر المتوفّرة فعلاً في DataFrame (خالص)."""
    if not isinstance(df, pd.DataFrame):
        return []
    return [c for c in _TABLE_COLS if c in df.columns]


def _build_ctrl_html(df: pd.DataFrame, state: AppState, section: SectionType) -> str:
    """يبني HTML شريط التحكم اللاصق (إحصائيات + حرجية + شفافية في سطر واحد مدمج)."""
    s = section_stats(df)
    tc = transparency_counts(df, state)
    cc = criticality_counts(df)
    accent = section_accent(section.value)
    crit_html = ""
    if cc["🔴 حرج"] or cc["🟡 متوسط"]:
        crit_html = (
            f'<span class="st-i" style="color:#EF4444">🔴 <b>{cc["🔴 حرج"]:,}</b> حرج</span>'
            f'<span class="st-i" style="color:#F59E0B">🟡 <b>{cc["🟡 متوسط"]:,}</b> متوسط</span>'
        )
    movers_html = ""
    if section is SectionType.PRICE_RAISE:
        try:
            from services.priority_engine import movers_count
            _n_movers = movers_count(df)
            if _n_movers > 0:
                movers_html = f'<span class="st-i" style="color:#8B5CF6">⚡ <b>{_n_movers:,}</b> تحرّكات اليوم</span>'
        except Exception:
            pass
    return (
        f'<div class="mhw-ctrl" style="--ac:{accent}">'
        f'<div class="st-row">'
        f'<span class="st-i">📊 <b>{int(s["count"]):,}</b> منتج</span>'
        f'<span class="st-i">Ø <b>{s["avg"]:,.0f}</b> ر.س</span>'
        f'<span class="st-i">↓ <b>{s["min"]:,.0f}</b> ر.س</span>'
        f'<span class="st-i">↑ <b>{s["max"]:,.0f}</b> ر.س</span>'
        f'{crit_html}'
        f'{movers_html}'
        f'<span class="st-tr">👁️ {tc["shown"]:,} معروض · '
        f'{tc["hidden"]:,} مخفي · {tc["processed"]:,} معالَج</span>'
        f'</div></div>'
    )


def _render_view(st: Any, view_items: pd.DataFrame, state: AppState,
                 section: SectionType, *, as_table: bool) -> None:
    """يعرض الصفحة الحالية كجدول أو بطاقات حَلَبة (مع إجراء سعري للأقسام السعرية)."""
    if as_table:
        cols = table_columns(view_items)
        st.dataframe(
            view_items[cols] if cols else view_items,
            hide_index=True, width="stretch",
        )
        return
    accent = section_accent(section.value)
    show_price_action = section in _PRICE_ACTION_SECTIONS
    is_excluded = section is SectionType.EXCLUDED
    from services.top_competitor_service import badge_html_for_name
    from services.store_profile_service import store_badge_for_name
    for _, row in view_items.iterrows():
        if is_excluded:
            # القسم المستبعد لا تطابق فيه — الفرق معروض لكنه ليس مقارنة حقيقية.
            st.markdown(
                '<div style="color:#F59E0B;font-size:.8rem;font-weight:600;'
                'margin:2px 0">⚠️ غير مطابق — للعرض فقط، ليست مقارنة حقيقية</div>',
                unsafe_allow_html=True,
            )
        render_row_arena(row, accent=accent)
        # سطر سبب الأولوية (قسم سعر أعلى فقط، عناصر تجاوزت بوّابة اليقين) — عرض فقط.
        _reason = str(row.get("_سبب_الأولوية") or "") if hasattr(row, "get") else ""
        if _reason:
            st.caption(f"⚡ {_reason}")
        # وسما التقييم (⭐ المنتج بالسوق بارز بالنجوم · 🏪 المتجر) — عرض فقط، فارغ آمن.
        _rate_html = badge_html_for_name(str(row.get("منتج_المنافس") or "")) if hasattr(row, "get") else ""
        _store = store_badge_for_name(str(row.get("المنافس") or "")) if hasattr(row, "get") else ""
        if _rate_html or _store:
            import streamlit as st
            if _rate_html:
                st.markdown(_rate_html, unsafe_allow_html=True)
            if _store:
                st.caption(_store)
        if show_price_action:
            render_price_action(row, state, section.value, key_prefix=section.value)
        st.write("")  # فاصل رفيع منظّم بين البطاقات


def _handle_bulk(
    state: AppState, page_df: pd.DataFrame, section_df: pd.DataFrame, key: str,
) -> None:
    """يعالج الإجراء الجماعي (حذف ناعم للصفحة؛ إرسال/تصدير للقسم كامله)."""
    action = render_action_bar(page_df, COL_OUR_NAME, key, section_df=section_df)
    if action.action is ActionType.HIDE:
        for name in action.keys:
            state.hide(name)
            state.log_action(key=name, name=name, action="تجاهل/إخفاء",
                             detail="أُزيل من القسم", kind="hidden")
        state.persist_results()
        import streamlit as st
        st.rerun()  # ⚡ إخلاء فوري — البطاقات المخفية تختفي من القسم



def _priority_sorted_cached(df: pd.DataFrame, key: str, section: SectionType | None = None) -> pd.DataFrame:
    """يرتّب القسم «الأهم أولاً» مرّة لكل (قسم، حجم) ويُخبّئ الناتج في session_state.

    الفرز الافتراضي (عدد المنافسين الأرخص منّا تنازلياً) يفكّ تفاصيل منافسي كل
    صفّ، فيُحسب **مرّة** لا كل تفاعل. لقسم «سعر أعلى» تحديداً يُستخدم محرك
    الأولوية الأغنى (``services.priority_engine.urgency_sorted``: الأثر المالي ×
    يقين الطلب × ثقة المصدر ببوابة يقين) — بقية الأقسام بلا تغيير. عرض فقط —
    لا يمسّ ``state.sections``. أي خطأ ⇒ df كما هو.
    """
    try:
        import streamlit as st
        from ui.components.criticality import priority_sorted
        if not isinstance(df, pd.DataFrame) or df.empty:
            return df
        cache_key = f"_prio_sorted::{key}::{len(df)}"
        cached = st.session_state.get(cache_key)
        if isinstance(cached, pd.DataFrame) and len(cached) == len(df):
            return cached
        if section is SectionType.PRICE_RAISE:
            from services.priority_engine import urgency_sorted
            out = urgency_sorted(df)
        else:
            out = priority_sorted(df)
        st.session_state[cache_key] = out
        return out
    except Exception:
        return df


def reset_page_on_filter_change(key: str, filters: Any) -> None:
    """يصفّر ترقيم القسم عند تغيّر أي فلتر — وإلا بقي المستخدم على صفحة فارغة.

    يقارن بصمة الفلاتر (repr لـdataclass مجمّدة) بالمحفوظة في session_state؛
    أول عرض يسجّل البصمة فقط دون تصفير (حفاظاً على أي حالة مستعادة).
    """
    import streamlit as st

    sig = repr(filters)
    sig_key = f"{key}_filters_sig"
    prev = st.session_state.get(sig_key)
    st.session_state[sig_key] = sig
    if prev is not None and prev != sig:
        st.session_state[f"{key}_page"] = 1


def render_section_page(
    state: AppState,
    sections: dict[str, pd.DataFrame],
    section: SectionType,
    *,
    per_page: int = 12,
) -> None:
    """يعرض قسماً كاملاً ببطاقات الحَلَبة (غلاف رفيع يستدعي المكوّنات).

    رأس ثابت ملوّن بلون القسم + شريط تحكم لاصق (إحصائيات + شفافية) +
    فلاتر مدمجة أفقياً + بطاقة حَلَبة لكل منتج + إجراءات جماعية.
    """
    import streamlit as st

    key = section.value
    # حماية من sections=None أثناء الانتقال (Error Boundary).
    safe_sections = sections if isinstance(sections, dict) else {}
    df = section_dataframe(safe_sections, section)
    # ── ترتيب «الأهم أولاً» (طلب المالك): الأكثر منافسين أرخص منّا في القمة ──
    # مُخبّأ لكل (قسم، حجم) لتفادي إعادة الحساب كل تفاعل؛ عرض فقط لا يمسّ المصدر.
    df = _priority_sorted_cached(df, key, section)
    render_page_header(section_badge(section)[0], section=section, count=len(df))
    if df.empty:
        if state.analysis_results is None:
            st.info("📋 لم تُجرِ تحليلاً بعد — اذهب إلى «📊 لوحة التحكم»، "
                    "ارفع كتالوجك، ثم اضغط «🚀 ابدأ التحليل».")
        else:
            st.info("لا منتجات في هذا القسم")
        return

    # ── فلاتر مدمجة أفقياً (بحث + ماركة + جنس في صف، متقدم في popover) ──
    filters = render_filter_bar(df, key)
    view_df = apply_filters(df, filters)
    view_df = filter_by_criticality(view_df, filters.criticality)  # فلتر الأهمية القصوى
    if filters.availability != "الكل":  # فلتر التوفّر (نفذت/متوفر) عبر روابط المنافسين
        from ui.components.comparison_card import _oos_links
        view_df = filter_by_availability(view_df, filters.availability, _oos_links())
    # ── شريط التحكم اللاصق (إحصائيات + شفافية) — بعد الفلاتر ليتفاعل معها ──
    # view_df لا visible_df: يُبقي صفوف المخفي/المعالَج داخل الحساب وإلا صفر عدّاد الشفافية دائماً.
    st.markdown(_build_ctrl_html(view_df, state, section), unsafe_allow_html=True)
    visible_df, _ = visible_dataframe(view_df, state, COL_OUR_NAME)
    # (شرائح الفلاتر النشطة تُعرض الآن داخل render_filter_bar نفسه)
    reset_page_on_filter_change(key, filters)

    as_table = st.toggle("📋 عرض كجدول", key=f"{key}_table")
    page = int(st.session_state.get(f"{key}_page", 1))
    view = paginate(visible_df, page, per_page)
    caption = view.caption
    if len(visible_df) != len(df):  # المخفي/المعالَج في عدّاد الشفافية أعلى القسم
        caption += f" · مفلتر من {len(df)}"
    st.caption(caption)
    _render_view(st, view.items, state, section, as_table=as_table)
    render_pagination(view, key)
    _handle_bulk(state, view.items, visible_df, key)

