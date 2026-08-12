"""ui/pages/review.py — قسم ⚠️ تحت المراجعة (مع شريط إجراءات تفاعلي).

يعرض المنتجات ذات الثقة المتوسطة (المطابقة غير حاسمة) مع 4 أزرار
لكل بطاقة تتيح للمستخدم حسم المنتج المعلّق:

  ✅ تأكيد التطابق  → ينتقل إلى سعر أعلى/أقل/موافق حسب الفرق.
  ❌ لا يطابق       → ينتقل إلى المستبعدات.
  🤖 حسم بالـ AI    → يسأل الذكاء الاصطناعي ويعرض النتيجة.
  🗑️ تجاهل          → يُخفى من القسم.

الإجراءات تُسجَّل في ``state.log_action`` وتُثبَّت على القرص فوراً.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from conf.constants import (
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_DIFF,
    COL_OUR_NAME,
    COL_OUR_PRICE,
)
from core.enums import ActionType, SectionType
from services.monitoring.data_quality import DataQualityScorer
from ui.components.action_bar import render_action_bar
from ui.components.comparison_card import render_row_arena
from ui.components.criticality import criticality_counts, filter_by_criticality
from ui.components.filter_bar import apply_filters, render_filter_bar
from ui.components.page_header import render_page_header, section_accent
from ui.components.pagination import paginate, render_pagination
from ui.components.product_card import parse_competitor_details
from ui.components.status_badge import section_badge
from ui.pages._section_page import (
    _build_ctrl_html,
    reset_page_on_filter_change,
    section_dataframe,
    visible_dataframe,
)
from ui.state_manager import AppState


# ══════════════════════════════════════════════════════════════════════
#  دوال خالصة (Pure — قابلة للاختبار بلا Streamlit)
# ══════════════════════════════════════════════════════════════════════

def _safe_get(row: Any, col: str, default: Any = "") -> Any:
    """قراءة آمنة من صف (Series أو dict)."""
    if hasattr(row, "get"):
        val = row.get(col, default)
        return default if val is None or str(val) == "nan" else val
    return default


_outlier_scorer = DataQualityScorer()


def has_outlier_competitor_price(row: Any) -> bool:
    """هل أحد أسعار منافسي هذا المنتج شاذ إحصائياً؟ (خالص)

    يمرّر أسعار منافسي الصف نفسه إلى ``detect_price_outliers``
    (z-score > 2) — تنبيه بصري فقط، القرار يبقى للمستخدم.
    """
    prices = [d["price"] for d in parse_competitor_details(row) if d.get("price")]
    if len(prices) < 3:
        return False
    mini_df = pd.DataFrame({"السعر": prices})
    return bool(_outlier_scorer.detect_price_outliers(mini_df, z_threshold=2.0))


def decide_target_section(row: Any) -> str:
    """يحدد القسم المستهدف عند تأكيد التطابق (خالص).

    - فرق موجب (سعرنا < منافس)  → price_raise
    - فرق سالب (سعرنا > منافس)  → price_lower
    - لا فرق / لا بيانات       → approved
    """
    try:
        diff = float(_safe_get(row, COL_DIFF, 0))
    except (TypeError, ValueError):
        try:
            our = float(_safe_get(row, COL_OUR_PRICE, 0))
            comp = float(_safe_get(row, COL_COMP_PRICE, 0))
            diff = comp - our if our > 0 and comp > 0 else 0
        except (TypeError, ValueError):
            diff = 0
    if diff > 0:
        return "price_raise"
    if diff < 0:
        return "price_lower"
    return "approved"


_TARGET_LABELS: dict[str, str] = {
    "price_raise": "🔴 سعر أعلى",
    "price_lower": "🟢 سعر أقل",
    "approved": "✅ موافق عليها",
    "excluded": "⚪ مستبعد",
    "missing": "🔍 مفقودات",
}


# ══════════════════════════════════════════════════════════════════════
#  إجراءات البطاقة (تعدّل state وتسجّل)
# ══════════════════════════════════════════════════════════════════════

def _move_product(
    row: Any,
    row_idx: int,
    state: AppState,
    from_section: str,
    to_section: str,
    reason: str,
) -> None:
    """ينقل منتجاً من قسم المراجعة إلى قسم آخر ويسجّل الحركة.

    القاعدة الصارمة: لا DELETE — فقط نقل بين DataFrames.
    """
    sections = state.sections if isinstance(state.sections, dict) else {}
    source_df = sections.get(from_section, pd.DataFrame())
    if source_df.empty:
        return

    # استخراج الصف المنقول
    if row_idx in source_df.index:
        moved = source_df.loc[[row_idx]].copy()
        sections[from_section] = source_df.drop(row_idx).reset_index(drop=True)
    else:
        return

    # إضافة للقسم المستهدف
    if to_section == "missing":
        if state.missing_df is not None and not state.missing_df.empty:
            state.missing_df = pd.concat(
                [state.missing_df, moved], ignore_index=True,
            )
        else:
            state.missing_df = moved.copy()
    else:
        target_df = sections.get(to_section, pd.DataFrame())
        sections[to_section] = pd.concat(
            [target_df, moved], ignore_index=True,
        )

    state.sections = sections

    # تسجيل الإجراء
    name = str(_safe_get(row, COL_OUR_NAME, _safe_get(row, COL_COMP_NAME, "—")))
    state.log_action(
        key=name,
        name=name,
        action=reason,
        detail=f"نُقل من {_TARGET_LABELS.get(from_section, from_section)} "
               f"إلى {_TARGET_LABELS.get(to_section, to_section)}",
        kind="review_action",
    )
    state.persist_results()


def _render_review_actions(
    row: Any,
    row_idx: int,
    state: AppState,
    *,
    ai_service: Optional[Any] = None,
) -> None:
    """يعرض شريط الإجراءات الأربعة أسفل بطاقة المراجعة."""
    import streamlit as st

    name = str(_safe_get(row, COL_OUR_NAME, _safe_get(row, COL_COMP_NAME, "—")))
    key_base = f"rv_{row_idx}"

    # ── تخطيط محسّن: أزرار أكبر وأوضح ──
    col_confirm, col_reject, col_ai, col_exclude = st.columns([3, 3, 3, 2])

    # ── 1. تأكيد التطابق (بارز) ──
    with col_confirm:
        target = decide_target_section(row)
        label = f"✅ تأكيد → {_TARGET_LABELS.get(target, 'موافق')}"
        if st.button(label, key=f"cfm_{key_base}", type="primary",
                     use_container_width=True):
            _move_product(row, row_idx, state, "review", target,
                          "تأكيد التطابق يدوياً")
            st.toast(f"✅ «{name[:35]}» ← {_TARGET_LABELS.get(target, target)}", icon="✅")
            st.rerun()

    # ── 2. لا يطابق (مستبعد) ──
    with col_reject:
        if st.button("❌ لا يطابق", key=f"rej_{key_base}",
                     use_container_width=True):
            _move_product(row, row_idx, state, "review", "excluded",
                          "رفض التطابق يدوياً")
            st.toast(f"❌ «{name[:35]}» ← مستبعد", icon="❌")
            st.rerun()

    # ── 3. حسم بالذكاء الاصطناعي ──
    with col_ai:
        ai_disabled = ai_service is None or not getattr(ai_service, "any_configured", False)
        if st.button("🤖 حسم بالـ AI", key=f"ai_{key_base}",
                     disabled=ai_disabled, use_container_width=True):
            _handle_ai_decision(row, row_idx, state, ai_service)

    # ── 4. تجاهل / استبعاد ──
    with col_exclude:
        if st.button("🗑️ تجاهل", key=f"exc_{key_base}",
                     use_container_width=True, help="تجاهل هذا المنتج"):
            state.hide(name)
            state.log_action(
                key=name, name=name, action="تجاهل من المراجعة",
                detail="أُخفي يدوياً", kind="hidden",
            )
            state.persist_results()
            st.toast(f"🗑️ «{name[:35]}» تم تجاهله", icon="🗑️")
            st.rerun()


def _handle_ai_decision(
    row: Any, row_idx: int, state: AppState, ai_service: Any,
) -> None:
    """يسأل AI ثم ينقل المنتج حسب الإجابة."""
    import streamlit as st

    our_name = str(_safe_get(row, COL_OUR_NAME, ""))
    comp_name = str(_safe_get(row, COL_COMP_NAME, ""))
    our_price = _safe_get(row, COL_OUR_PRICE, 0)
    comp_price = _safe_get(row, COL_COMP_PRICE, 0)

    prompt = (
        f"منتجنا: «{our_name}» بسعر {our_price} ر.س\n"
        f"منتج المنافس: «{comp_name}» بسعر {comp_price} ر.س\n\n"
        "هل هذان المنتجان متطابقان (نفس العطر/المنتج بصيغة مختلفة)؟\n"
        "أجب بكلمة واحدة فقط في السطر الأول: [MATCH] أو [NO_MATCH]\n"
        "ثم في السطر الثاني اذكر السبب بإيجاز."
    )
    system = (
        "أنت خبير مطابقة منتجات عطور. حدّد إن كان منتجان متطابقين "
        "(نفس المنتج بأسماء/صيغ مختلفة) أم مختلفين. كن دقيقاً وحاسماً."
    )

    try:
        with st.spinner("🤖 يحلّل الذكاء الاصطناعي…"):
            result = ai_service.call(prompt, system)

        if not result.success:
            st.warning(f"⚠️ تعذّر التحقّق: {result.response or 'خطأ غير معروف'}")
            return

        response = (result.response or "").strip().upper()
        first_line = response.split("\n")[0].strip()

        if "MATCH" in first_line and "NO" not in first_line:
            # متطابق → نقل للقسم السعري المناسب
            target = decide_target_section(row)
            _move_product(row, row_idx, state, "review", target,
                          f"تأكيد AI: متطابق → {target}")
            st.success(f"🤖 **متطابق** — نُقل إلى {_TARGET_LABELS.get(target, target)}")
            st.caption(result.response)
            st.rerun()
        elif "NO" in first_line:
            # غير متطابق → مستبعد
            _move_product(row, row_idx, state, "review", "excluded",
                          "رفض AI: غير متطابق")
            st.info(f"🤖 **غير متطابق** — نُقل إلى المستبعدات")
            st.caption(result.response)
            st.rerun()
        else:
            # غير حاسم → يعرض النتيجة فقط بدون نقل
            st.info(f"🤖 **غير حاسم** — يبقى في المراجعة")
            st.caption(result.response)

    except Exception as exc:
        st.error(f"❌ خطأ في الاتصال بالذكاء الاصطناعي: {exc}")


# ══════════════════════════════════════════════════════════════════════
#  نقطة الدخول
# ══════════════════════════════════════════════════════════════════════

def render(
    state: AppState,
    sections: dict[str, pd.DataFrame],
    *,
    ai_service: Optional[Any] = None,
) -> None:
    """يعرض قسم «تحت المراجعة» مع شريط إجراءات تفاعلي لكل بطاقة."""
    import streamlit as st

    section = SectionType.REVIEW
    key = section.value

    safe_sections = sections if isinstance(sections, dict) else {}
    df = section_dataframe(safe_sections, section)
    render_page_header(section_badge(section)[0], section=section, count=len(df))
    if df.empty:
        if state.analysis_results is None:
            st.info("📋 لم تُجرِ تحليلاً بعد — اذهب إلى «📊 لوحة التحكم»، "
                    "ارفع كتالوجك، ثم اضغط «🚀 ابدأ التحليل».")
        else:
            st.info("لا منتجات في هذا القسم")
        return

    # ── فلاتر ──
    filters = render_filter_bar(df, key)
    view_df = apply_filters(df, filters)
    view_df = filter_by_criticality(view_df, filters.criticality)
    # ── شريط التحكم اللاصق (إحصائيات + شفافية) — بعد الفلاتر ليتفاعل معها ──
    # view_df لا visible_df: يُبقي صفوف المخفي/المعالَج داخل الحساب وإلا صفر عدّاد الشفافية دائماً.
    st.markdown(_build_ctrl_html(view_df, state, section), unsafe_allow_html=True)
    visible_df, _ = visible_dataframe(view_df, state, COL_OUR_NAME)
    reset_page_on_filter_change(key, filters)

    as_table = st.toggle("📋 عرض كجدول", key=f"{key}_table")
    page = int(st.session_state.get(f"{key}_page", 1))
    view = paginate(visible_df, page, per_page=12)

    caption = view.caption
    if len(visible_df) != len(df):
        caption += f" · مفلتر من {len(df)}"
    st.caption(caption)

    # ── عرض البطاقات مع شريط الإجراءات ──
    accent = section_accent(key)
    if as_table:
        from ui.pages._section_page import table_columns
        cols = table_columns(view.items)
        display = (view.items[cols] if cols else view.items).copy()
        display.insert(0, "⚠️ شذوذ", view.items.apply(
            lambda r: "⚠️" if has_outlier_competitor_price(r) else "", axis=1,
        ))
        st.dataframe(display, hide_index=True, width="stretch")
    else:
        for original_idx, row in view.items.iterrows():
            if has_outlier_competitor_price(row):
                st.warning("⚠️ سعر منافس شاذ إحصائياً بين أسعار منافسي هذا "
                           "المنتج — راجع الأسعار قبل الحسم")
            render_row_arena(row, accent=accent)
            _render_review_actions(
                row, original_idx, state,
                ai_service=ai_service,
            )
            st.write("")  # فاصل رفيع

    render_pagination(view, key)
    _handle_bulk(state, view.items, visible_df, key)


def _handle_bulk(
    state: AppState, page_df: pd.DataFrame, section_df: pd.DataFrame, key: str,
) -> None:
    """إجراءات جماعية (حذف ناعم)."""
    action = render_action_bar(page_df, COL_OUR_NAME, key, section_df=section_df)
    if action.action is ActionType.HIDE:
        for name in action.keys:
            state.hide(name)
            state.log_action(key=name, name=name, action="تجاهل/إخفاء",
                             detail="أُزيل من قسم المراجعة", kind="hidden")
        state.persist_results()
        import streamlit as st
        st.rerun()
