"""ui/pages/excluded.py — قسم ⚪ مستبعد (مع 🧠 إحياء بالذكاء الاصطناعي).

يعرض المنتجات المستبعدة (لا تطابق) مع زر «🧠 فحص بالذكاء الاصطناعي» الذي
يُرسل دفعة (500 منتج) إلى AI Router لإعادة تقييمها. المنتجات المُحياة تنتقل
إلى الأقسام السعرية أو المفقودات، والباقي يبقى.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from core.enums import SectionType
from ui.pages._section_page import render_section_page
from ui.state_manager import AppState


def _load_pool_cached() -> pd.DataFrame:
    """يحمّل كتالوج المنافسين العطري مرّة، ويُعيد بناءه إذا تغيّرت القاعدة (بعد كشطة)."""
    import os

    import streamlit as st

    from conf.constants import COMPETITOR_DB_PATH
    from services.deterministic_salvage import load_perfume_competitor_pool

    try:
        sig = os.path.getmtime(str(COMPETITOR_DB_PATH))
    except OSError:
        sig = 0.0

    @st.cache_data(show_spinner=False)
    def _cached(signature: float) -> pd.DataFrame:
        return load_perfume_competitor_pool()

    return _cached(sig)


def _run_deterministic_salvage(state: AppState) -> None:
    """إنقاذ حتمي (بلا ذكاء/بلا API): ينقل المطابق القوي من «مستبعد» إلى «مراجعة»."""
    import streamlit as st

    from services.deterministic_salvage import apply_deterministic_salvage

    sections = state.sections if isinstance(state.sections, dict) else {}
    excluded_df = sections.get("excluded", pd.DataFrame())
    if excluded_df.empty:
        st.warning("لا منتجات مستبعدة لفحصها")
        return

    with st.spinner("🔎 جاري تحميل كتالوج المنافسين…"):
        pool = _load_pool_cached()
    if pool is None or pool.empty:
        st.error("تعذّر تحميل كتالوج المنافسين — لم يُجرَ أي إنقاذ")
        return

    progress = st.progress(0, text="🔎 جاري البحث عن نظير لكل عطر مستبعد…")

    def _update(done: int, total: int) -> None:
        progress.progress(min(done / max(total, 1), 1.0),
                          text=f"🔎 فحص {done:,}/{total:,} عطر…")

    # النواة المشتركة: تنقل الصفوف وتحدّث عدّاد لوحة التحكم (section_counts) معاً
    n_match = apply_deterministic_salvage(
        sections, result=state.analysis_results, pool=pool, progress_cb=_update,
    )
    progress.empty()

    if n_match > 0:
        state.sections = sections
        state.persist_results()
        st.success(
            f"🔎 **{n_match:,}** عطراً مستبعداً وُجد له نظير قوي وانتقل إلى "
            f"⚠️ تحت المراجعة (بلا تسعير تلقائي — قرار السعر لك، وقابل للتراجع)."
        )
    else:
        st.info("✅ لم يُعثر على نظير يبلغ عتبة الثقة الصارمة بين المستبعدات.")


def _run_salvage(state: AppState, container: Any) -> None:
    """يشغّل إحياء AI على المستبعدات (يُستدعى عند ضغط الزر)."""
    import streamlit as st

    from services.ai_router_service import AIRouterService

    sections = state.sections if isinstance(state.sections, dict) else {}
    excluded_df = sections.get("excluded", pd.DataFrame())
    if excluded_df.empty:
        st.warning("لا منتجات مستبعدة لفحصها")
        return

    router = AIRouterService(container.ai)
    progress = st.progress(0, text="🧠 جاري الفحص بالذكاء الاصطناعي…")

    def _update_progress(done: int, total: int) -> None:
        pct = min(done / max(total, 1), 1.0)
        progress.progress(pct, text=f"🧠 فحص {done:,}/{total:,} منتج…")

    result = router.salvage_excluded(
        excluded_df, max_items=500, progress_cb=_update_progress,
    )
    progress.empty()

    # ── تطبيق النتائج على state.sections ──
    n_match = len(result.salvaged_matches)
    n_miss = len(result.salvaged_missing)

    if n_match > 0 or n_miss > 0:
        # المطابقات تُضاف للأقسام السعرية المناسبة (مبدئياً "review" للمراجعة البشرية)
        if n_match > 0:
            old_review = sections.get("review", pd.DataFrame())
            sections["review"] = pd.concat(
                [old_review, result.salvaged_matches], ignore_index=True,
            )
        # المفقودات تُضاف لـ missing_df
        if n_miss > 0 and state.missing_df is not None:
            state.missing_df = pd.concat(
                [state.missing_df, result.salvaged_missing], ignore_index=True,
            )
        elif n_miss > 0:
            state.missing_df = result.salvaged_missing

        # تحديث المستبعدات (الباقي فقط)
        sections["excluded"] = result.stayed_excluded
        state.sections = sections
        state.persist_results()

    # ── ملخص النتائج ──
    if n_match == 0 and n_miss == 0:
        st.info(f"✅ تم فحص {result.total_checked:,} منتج — لم يُكتشف أي منتج قابل للإحياء")
    else:
        st.success(
            f"🧠 تم فحص {result.total_checked:,} منتج:\n"
            f"- **{n_match:,}** مُطابق (انتقل لـ ⚠️ مراجعة)\n"
            f"- **{n_miss:,}** مفقود (انتقل لـ 🔍 مفقودات)\n"
            f"- **{len(result.stayed_excluded):,}** بقي مستبعداً"
        )
    if result.errors:
        with st.expander(f"⚠️ {len(result.errors)} خطأ أثناء الفحص"):
            for err in result.errors[:20]:
                st.caption(err)


def render(
    state: AppState,
    sections: dict[str, pd.DataFrame],
    *,
    container: Optional[Any] = None,
) -> None:
    """يعرض قسم «مستبعد» مع زرّي إنقاذ: حتمي (دائماً) + ذكاء اصطناعي (إن توفّر)."""
    import streamlit as st

    # ── زر الإنقاذ الحتمي (بلا API — يعمل دائماً ما دام هناك مستبعدات) ──
    _excluded_count = len(sections.get("excluded", pd.DataFrame()))
    if _excluded_count > 0:
        d1, d2 = st.columns([3, 1])
        with d1:
            st.markdown(
                '<div style="background:#0F2A1E;border:1px solid #14532D;'
                'border-radius:12px;padding:12px 16px;margin-bottom:12px;'
                'display:flex;align-items:center;gap:10px">'
                '<span style="font-size:1.5rem">🔎</span>'
                '<div>'
                '<div style="font-weight:700;color:#E2E8F0;font-size:.95rem">'
                'إنقاذ فوري بلا ذكاء اصطناعي</div>'
                '<div style="font-size:.78rem;color:#94A3B8">'
                'يبحث عن نظير حقيقي لكل عطر مستبعد بمطابقة صارمة، وينقل المؤكّد '
                'إلى «تحت المراجعة» — مجاناً وقابل للتراجع</div>'
                '</div></div>',
                unsafe_allow_html=True,
            )
        with d2:
            if st.button("🔎 إنقاذ فوري", key="salvage_excluded_det",
                         type="primary", use_container_width=True):
                _run_deterministic_salvage(state)

    # ── زر الإحياء (يظهر فقط إن توفّرت حاوية AI) ──
    if container is not None and container.ai.any_configured:
        excluded_count = len(sections.get("excluded", pd.DataFrame()))
        if excluded_count > 0:
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(
                    f'<div style="background:#1E293B;border:1px solid #334155;'
                    f'border-radius:12px;padding:12px 16px;margin-bottom:12px;'
                    f'display:flex;align-items:center;gap:10px">'
                    f'<span style="font-size:1.5rem">🧠</span>'
                    f'<div>'
                    f'<div style="font-weight:700;color:#E2E8F0;font-size:.95rem">'
                    f'إحياء المستبعدات بالذكاء الاصطناعي</div>'
                    f'<div style="font-size:.78rem;color:#94A3B8">'
                    f'يفحص 500 منتج ويكتشف المطابق أو المفقود بالخطأ</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
            with c2:
                batch_label = f"🧠 فحص {min(excluded_count, 500):,} منتج تالي"
                if st.button(batch_label, key="salvage_excluded",
                             type="primary", use_container_width=True):
                    _run_salvage(state, container)

    render_section_page(state, sections, SectionType.EXCLUDED)
