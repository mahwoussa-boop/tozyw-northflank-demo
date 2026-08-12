"""ui/pages/redistribute.py — صفحة إعادة التوزيع بالذكاء الاصطناعي (Dry-Run Mode).

تعرض إحصائيات المنتجات العالقة، تشغّل التحليل عبر AI، ثم تعرض
جدول الحركات المقترحة للمستخدم قبل تطبيقها — «وضع المحاكاة».

التدفق:
  1. عرض إحصائيات الأقسام الثلاثة (مراجعة، مستبعد، مفقود).
  2. زر «🔄 تحليل وبناء خطة التوزيع» → يبني الخطة (Dry-Run).
  3. عرض جدول الحركات المقترحة (المنتج | من | إلى | السبب).
  4. زر «✅ اعتماد التوزيع» → يطبّق الخطة فعلياً.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ui.state_manager import AppState


# ── تسميات الأقسام ────────────────────────────────────────────────────
_SECTION_LABELS: dict[str, str] = {
    "review": "⚠️ تحت المراجعة",
    "excluded": "⚪ مستبعد",
    "missing": "🔍 مفقود",
    "approved": "✅ موافق عليها",
    "price_raise": "🔴 سعر أعلى",
    "price_lower": "🟢 سعر أقل",
}

_SECTION_COLORS: dict[str, str] = {
    "review": "#F59E0B",
    "excluded": "#6B7280",
    "missing": "#3B82F6",
    "approved": "#10B981",
    "price_raise": "#EF4444",
    "price_lower": "#22C55E",
}


def _count_pending(state: AppState) -> dict[str, int]:
    """يحسب عدد المنتجات العالقة في كل قسم (خالص — بلا Streamlit).

    المفقودات تُقسّم إلى: مؤكدة (لا تحتاج إعادة توزيع) وغير مؤكدة (تحتاج).
    """
    sections = state.sections if isinstance(state.sections, dict) else {}
    missing_df = state.missing_df

    # ── تفصيل المفقودات حسب مستوى الثقة ──
    missing_total = 0
    missing_confirmed = 0  # green = مؤكدة → لا تحتاج إعادة توزيع
    missing_pending = 0    # review/other = تحتاج مراجعة
    if missing_df is not None and not missing_df.empty:
        missing_total = len(missing_df)
        conf_col = "مستوى_الثقة"
        if conf_col in missing_df.columns:
            level = missing_df[conf_col].astype(str)
            missing_confirmed = int((level == "green").sum())
            missing_pending = missing_total - missing_confirmed
        else:
            # لا عمود ثقة → كلها عالقة
            missing_pending = missing_total

    counts = {
        "review": len(sections.get("review", pd.DataFrame())),
        "excluded": len(sections.get("excluded", pd.DataFrame())),
        "missing_pending": missing_pending,
        "missing_confirmed": missing_confirmed,
        "missing_total": missing_total,
    }
    return counts


def _render_pending_cards(counts: dict[str, int]) -> None:
    """يعرض بطاقات إحصائيات المنتجات العالقة مع تفصيل دقيق للمفقودات."""
    import streamlit as st

    # المنتجات التي تحتاج إعادة توزيع فعلاً (بدون المفقودات المؤكدة)
    actionable = counts["review"] + counts["excluded"] + counts["missing_pending"]

    # ── بطاقات الأقسام العالقة (3 بطاقات رئيسية) ──
    pending_cards = [
        ("⚠️ تحت المراجعة", counts["review"], "#F59E0B"),
        ("⚪ مستبعد", counts["excluded"], "#6B7280"),
        ("🔍 مفقود (غير مؤكد)", counts["missing_pending"], "#3B82F6"),
    ]

    cards_html = ""
    for label, n, color in pending_cards:
        pct = (n / actionable * 100) if actionable > 0 else 0
        cards_html += (
            f'<div style="flex:1;min-width:160px;background:rgba(22,22,26,.92);'
            f'border:1px solid #1F2937;border-top:3px solid {color};'
            f'border-radius:12px;padding:14px 16px;text-align:center">'
            f'<div style="font-size:.78rem;color:#94A3B8;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:2rem;font-weight:900;color:#E2E8F0;line-height:1.1">'
            f'{n:,}</div>'
            f'<div style="font-size:.72rem;color:{color};font-weight:700;margin-top:3px">'
            f'{pct:.1f}% من العالقة</div>'
            f'</div>'
        )

    # ── بطاقة المفقودات المؤكدة (منفصلة — خضراء — لا تحتاج إعادة توزيع) ──
    confirmed = counts["missing_confirmed"]
    if confirmed > 0:
        cards_html += (
            f'<div style="flex:1;min-width:160px;background:rgba(16,185,129,.08);'
            f'border:1px solid #065F46;border-top:3px solid #10B981;'
            f'border-radius:12px;padding:14px 16px;text-align:center">'
            f'<div style="font-size:.78rem;color:#6EE7B7;margin-bottom:4px">'
            f'✅ مفقود مؤكد (جاهز)</div>'
            f'<div style="font-size:2rem;font-weight:900;color:#A7F3D0;line-height:1.1">'
            f'{confirmed:,}</div>'
            f'<div style="font-size:.72rem;color:#10B981;font-weight:700;margin-top:3px">'
            f'لا يحتاج إعادة توزيع</div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;direction:rtl;'
        f'font-family:Tajawal,sans-serif;margin:8px 0 16px">{cards_html}</div>',
        unsafe_allow_html=True,
    )

    # شريط ملخّص
    if actionable > 0:
        total_all = counts["missing_total"] + counts["review"] + counts["excluded"]
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#1E293B,#0F172A);'
            f'border:1px solid #334155;border-radius:10px;padding:10px 16px;'
            f'text-align:center;font-family:Tajawal,sans-serif;margin-bottom:12px;'
            f'display:flex;gap:20px;justify-content:center;flex-wrap:wrap;direction:rtl">'
            f'<span style="color:#F59E0B;font-size:.88rem">'
            f'<b style="font-size:1.1rem;font-weight:900">{actionable:,}</b> '
            f'<span style="color:#94A3B8">منتج يحتاج إعادة توزيع</span></span>'
            f'<span style="color:#64748B">|</span>'
            f'<span style="color:#10B981;font-size:.88rem">'
            f'<b style="font-size:1.1rem;font-weight:900">{confirmed:,}</b> '
            f'<span style="color:#94A3B8">مؤكد جاهز للتصدير</span></span>'
            f'<span style="color:#64748B">|</span>'
            f'<span style="color:#94A3B8;font-size:.82rem">'
            f'إجمالي: {total_all:,}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.success("✅ لا منتجات عالقة — جميع الأقسام نظيفة!")


def _render_plan_table(plan: Any) -> None:
    """يعرض جدول الحركات المقترحة من الخطة."""
    import streamlit as st

    if plan.is_empty:
        if plan.errors:
            st.error("❌ حدثت أخطاء أثناء التحليل:")
            for err in plan.errors:
                st.markdown(f"- `{err}`")
        else:
            st.info(
                "💡 لم يُكتشف أي حركات مقترحة — المنتجات في أقسامها الصحيحة حسب نسب التطابق.\n\n"
                f"**تفاصيل:** مراجعة: {plan.reviews_processed} · "
                f"مستبعد: {plan.excluded_processed} · "
                f"مفقود: {plan.missing_processed}"
            )
        return

    # ── ملخّص الخطة ──
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#065F46,#064E3B);'
        f'border:1px solid #059669;border-radius:12px;padding:14px 18px;'
        f'font-family:Tajawal,sans-serif;margin-bottom:12px">'
        f'<div style="color:#D1FAE5;font-weight:800;font-size:1.05rem;margin-bottom:6px">'
        f'📋 خطة إعادة التوزيع — {plan.total_moved:,} حركة مقترحة</div>'
        f'<div style="color:#A7F3D0;font-size:.82rem;display:flex;gap:16px;flex-wrap:wrap;direction:rtl">'
        f'<span>⚠️ مراجعة: {plan.reviews_upgraded + plan.reviews_to_excluded + plan.reviews_to_missing} حركة</span>'
        f'<span>⚪ مستبعد: {plan.excluded_salvaged + plan.excluded_to_missing} حركة</span>'
        f'<span>🔍 مفقود: {plan.missing_deduplicated} حركة</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── جدول الحركات ──
    moves_data = []
    for move in plan.moves:
        if move.old_section == move.new_section:
            continue
        moves_data.append({
            "المنتج": move.product_name[:60],
            "من": _SECTION_LABELS.get(move.old_section, move.old_section),
            "إلى": _SECTION_LABELS.get(move.new_section, move.new_section),
            "السبب": move.ai_reason,
            "الثقة": f"{move.ai_confidence:.0%}",
        })

    if moves_data:
        df = pd.DataFrame(moves_data)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            height=min(400, 38 * len(moves_data) + 38),
        )

    # ── إحصائيات تفصيلية ──
    with st.expander("📊 إحصائيات تفصيلية", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("⚠️ مراجعة مُعالجة", plan.reviews_processed)
            st.caption(f"↗️ ترقية: {plan.reviews_upgraded} · ⚪ مستبعد: {plan.reviews_to_excluded} · ⏸️ بقي: {plan.reviews_stayed}")
        with col2:
            st.metric("⚪ مستبعد مُعالج", plan.excluded_processed)
            st.caption(f"♻️ أُنقذ: {plan.excluded_salvaged} · ↘️ مفقود: {plan.excluded_to_missing} · 🔒 مقفل: {plan.excluded_locked}")
        with col3:
            st.metric("🔍 مفقود مُعالج", plan.missing_processed)
            st.caption(f"🔁 تكرار: {plan.missing_deduplicated} · ✅ مؤكد: {plan.missing_confirmed}")

    # ── الأخطاء ──
    if plan.errors:
        with st.expander(f"⚠️ {len(plan.errors)} خطأ أثناء التحليل", expanded=False):
            for err in plan.errors[:30]:
                st.caption(f"• {err}")


def _run_build_plan(state: AppState, container: Any) -> None:
    """يبني خطة إعادة التوزيع (Dry-Run) ويحفظها في session_state."""
    import streamlit as st

    from infrastructure.ai_decisions_repo import AIDecisionRepository
    from services.ai_redistributor import AIRedistributor

    repo = AIDecisionRepository(container.db)
    repo.ensure_table()
    redistributor = AIRedistributor(container.ai, repo)

    sections = state.sections if isinstance(state.sections, dict) else {}

    progress_bar = st.progress(0, text="🔄 بدء التحليل…")
    status_text = st.empty()

    stage_weights = {"مراجعة": 0.4, "مستبعد": 0.4, "مفقودات": 0.2}
    stage_starts = {"مراجعة": 0.0, "مستبعد": 0.4, "مفقودات": 0.8}

    def _progress(stage: str, done: int, total: int) -> None:
        if total <= 0:
            return
        base = stage_starts.get(stage, 0.0)
        weight = stage_weights.get(stage, 0.2)
        pct = base + weight * min(done / total, 1.0)
        progress_bar.progress(min(pct, 1.0), text=f"🔄 {stage}: {done:,}/{total:,} منتج…")
        status_text.caption(f"المرحلة: {stage} — {done:,} من {total:,}")

    plan = redistributor.build_plan(
        sections=sections,
        missing_df=state.missing_df,
        our_catalog=state.our_catalog,
        progress_cb=_progress,
    )

    progress_bar.progress(1.0, text="✅ اكتمل التحليل")
    status_text.empty()

    st.session_state["_redistribution_plan"] = plan


def _run_apply_plan(state: AppState, container: Any) -> None:
    """ينفّذ خطة إعادة التوزيع المعتمدة."""
    import streamlit as st

    from infrastructure.ai_decisions_repo import AIDecisionRepository
    from services.ai_redistributor import AIRedistributor

    plan = st.session_state.get("_redistribution_plan")
    if plan is None or plan.is_empty:
        st.warning("لا توجد خطة للتنفيذ")
        return

    repo = AIDecisionRepository(container.db)
    repo.ensure_table()
    redistributor = AIRedistributor(container.ai, repo)

    sections = state.sections if isinstance(state.sections, dict) else {}

    with st.spinner("⏳ جاري تطبيق خطة إعادة التوزيع…"):
        new_sections, new_missing, report = redistributor.apply_plan(
            plan, sections, state.missing_df,
        )

    state.sections = new_sections
    state.missing_df = new_missing
    state.persist_results()

    # مسح الخطة من الجلسة
    st.session_state.pop("_redistribution_plan", None)

    # ── عرض تقرير التنفيذ ──
    st.success(
        f"✅ تم تنفيذ إعادة التوزيع بنجاح!\n\n"
        f"- **{report.applied_moves:,}** منتج تم نقله\n"
        f"- **{report.decisions_logged:,}** قرار مسجّل في قاعدة البيانات\n"
        f"- معرّف الدفعة: `{report.batch_id}`"
    )
    if report.errors:
        with st.expander(f"⚠️ {len(report.errors)} خطأ أثناء التنفيذ"):
            for err in report.errors[:20]:
                st.caption(f"• {err}")


def render(
    state: AppState,
    *,
    container: Optional[Any] = None,
) -> None:
    """يعرض صفحة إعادة التوزيع بالذكاء الاصطناعي."""
    import streamlit as st

    from ui.components.page_header import render_page_header

    render_page_header("إعادة التوزيع بالذكاء الاصطناعي", section="redistribute")

    # ── شرح الصفحة ──
    st.markdown(
        '<div style="background:linear-gradient(135deg,#1E293B,#0F172A);'
        'border:1px solid #334155;border-radius:14px;padding:16px 20px;'
        'font-family:Tajawal,sans-serif;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px;direction:rtl">'
        '<span style="font-size:2rem">🔄</span>'
        '<div>'
        '<div style="font-weight:800;color:#E2E8F0;font-size:1.05rem">'
        'محرك إعادة التوزيع الذكي</div>'
        '<div style="font-size:.82rem;color:#94A3B8;line-height:1.5;margin-top:4px">'
        'يحلّل المنتجات العالقة في أقسام المراجعة والمستبعد والمفقودات، '
        'ويقترح إعادة توزيعها بالذكاء الاصطناعي. '
        '<b style="color:#F59E0B">وضع المحاكاة:</b> '
        'يعرض الحركات المقترحة قبل تنفيذها — أنت تقرر.'
        '</div></div></div></div>',
        unsafe_allow_html=True,
    )

    # ── إحصائيات المنتجات العالقة ──
    counts = _count_pending(state)
    _render_pending_cards(counts)

    total_pending = counts["review"] + counts["excluded"] + counts["missing_pending"]
    if total_pending == 0:
        return

    # ── التحقق من وجود AI ──
    if container is None or not container.ai.any_configured:
        st.warning(
            "⚠️ لا يوجد مزوّد ذكاء اصطناعي مُهيّأ. "
            "يرجى إضافة مفاتيح API في ملف `.env` لتشغيل المحرك."
        )
        return

    # ── زر بناء الخطة ──
    plan = st.session_state.get("_redistribution_plan")

    if plan is None:
        st.markdown("---")
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button(
                "🔄 تحليل وبناء خطة التوزيع",
                type="primary",
                use_container_width=True,
                key="btn_build_plan",
            ):
                _run_build_plan(state, container)
                st.rerun()
    else:
        # ── عرض الخطة المقترحة ──
        st.markdown("---")
        _render_plan_table(plan)

        if not plan.is_empty:
            st.markdown("---")
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                c_approve, c_cancel = st.columns(2)
                with c_approve:
                    if st.button(
                        "✅ اعتماد التوزيع",
                        type="primary",
                        use_container_width=True,
                        key="btn_apply_plan",
                    ):
                        _run_apply_plan(state, container)
                        st.rerun()
                with c_cancel:
                    if st.button(
                        "❌ إلغاء الخطة",
                        use_container_width=True,
                        key="btn_cancel_plan",
                    ):
                        st.session_state.pop("_redistribution_plan", None)
                        st.rerun()
        else:
            # الخطة فارغة — زر لمسحها
            if st.button("🔙 رجوع", key="btn_back_empty"):
                st.session_state.pop("_redistribution_plan", None)
                st.rerun()
