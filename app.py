"""app.py — موجّه «مهووس v2» (Router + DI فقط).

≤150 سطراً، بلا منطق عمل. الملف الوحيد الذي يستورد Streamlit علوياً؛
كل وحدة أخرى تستورده كسولاً. الموجّه: شريط جانبي يوزّع على صفحات رفيعة،
وحاوية اعتماديات تحقن الخدمات. لا st.rerun خارج fragment.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # الاستيراد العلوي الوحيد لـ Streamlit في المشروع كله

from bootstrap import (
    Container,
    build_container,
    populate_our_catalog,
    run_missing_analysis,
    run_pricing_analysis,
    run_v4_post_analysis,
)
from ui.pages import (
    approved,
    dashboard,
    excluded,
    make_automation,
    missing,
    new_at_competitors,
    opportunity_radar,
    price_lower,
    price_raise,
    processed,
    product_factory,
    redistribute,
    review,
    scraper,
    settings,
    smart_automation,
    trash_bin,
)
from services.warmup import start_background_warmup
from ui.reconcile import reconcile_state
from ui.state_manager import AppState, StateStore, StreamlitStore

st.set_page_config(
    page_title="مهووس — التسعير الذكي v2", page_icon="🧪", layout="wide",
)

# تسخين مسبق لمُطابِق الملكية في خيط خلفي (محروس بمرّة واحدة لكل عملية).
# ينطلق عند **أول جلسة متصفّح** لا عند إقلاع الخادم (Streamlit لا ينفّذ هذا
# الملف قبل اتصال جلسة — مقيس حيّاً)، فيبني المذكّرة بينما يقرأ المالك صفحته
# الأولى. لا يمسّ منطقاً ولا يكتب شيئاً — تفاصيله في services/warmup.py.
start_background_warmup()

# بوابة كود الدخول أُزيلت بقرار المالك (2026-07-09) — التطبيق محلي/شبكة منزلية.


@st.cache_resource(show_spinner=False)
def _container() -> Container:
    """حاوية الاعتماديات (تُبنى مرة واحدة لكل جلسة خادم)."""
    return build_container()


# انتقل منطق الملء وv4 إلى bootstrap.py (طبقة خدمات نقية يشاركها مشغّل الخلفية).
_populate_our_catalog = populate_our_catalog


def _make_analyze(
    state: AppState, store: StateStore, container: Container,
) -> Callable[[Any], None]:
    """رد نداء التحليل الكامل: كتالوج → مفقودات → تسعير → v4 ذكاء → تخزين."""

    def _run(uploaded: Any) -> None:
        import time
        from core.enums import SectionType
        from services.catalog_service import load_catalog

        _t_start = time.time()
        try:
            with st.spinner("⏳ تحميل الكتالوج وكشف المفقودات (~دقيقتان لأول مرة)…"):
                our_df = load_catalog(uploaded)
                state.our_catalog = our_df
                # مرجع فحص الماركات = ماركات متجرنا الحقيقية (دقّة فحص «ماركة مفقودة»)
                from services.brand_manager import save_store_brands
                save_store_brands(our_df)
                # ملء our_catalog من نفس الكتالوج ليعمل مطابق الملكية على مرجع حقيقي.
                _populate_our_catalog(our_df)
                missing_df, mstats = run_missing_analysis(container, our_df)
            state.missing_df = missing_df

            with st.spinner("⏳ التحليل السعري الكامل: مطابقة المنافسين (~دقائق لأول مرة)…"):
                sections, result, missing_clean, astats = run_pricing_analysis(
                    container, our_df, missing_df=missing_df,
                )
            # ── 🔎 إنقاذ حتمي تلقائي: ينقذ العطور المستبعدة بإلزام الماركة إلى «مراجعة» ──
            # يتخطّى حجب الماركة في المحرّك (بلا لمسه) وينقل المطابق القوي فقط —
            # قابل للتراجع، بلا تسعير تلقائي. محروس: فشله لا يكسر التحليل.
            try:
                from services.deterministic_salvage import apply_deterministic_salvage
                with st.spinner("🔎 إنقاذ العطور المستبعدة بإلزام الماركة…"):
                    n_salvaged = apply_deterministic_salvage(sections, result=result)
                if n_salvaged:
                    astats["deterministic_salvaged"] = n_salvaged
            except Exception as _exc:  # noqa: BLE001
                astats["salvage_error"] = str(_exc)[:200]
            state.sections = sections
            state.missing_df = missing_clean
            try:
                result.duration_sec = round(time.time() - _t_start, 1)
            except Exception:
                pass
            state.analysis_results = result
            reconcile_state(state)

            # ── v4: تفعيل الذكاء الحقيقي بعد التحليل ──────────────────
            _run_v4_post_analysis(container, sections, result)

            state.persist_results()

            counts = result.section_counts
            recovered = int(astats.get("catalog_recovered", 0) or 0)
            recovered_txt = f" · ♻️ {recovered:,} مُستردّ" if recovered else ""
            # نتيجة الإنقاذ الحتمي كانت تُكتب في astats ولا تُقرأ أبداً: نجاحه
            # وفشله سواء في الصمت. نُظهر العدد، ونُبرز الفشل تحذيراً مرئياً كي لا
            # تبقى مستبعدات قابلة للإنقاذ بلا علم المالك.
            salvaged = int(astats.get("deterministic_salvaged", 0) or 0)
            salvaged_txt = f" · 🔎 {salvaged:,} مُنقَذ للمراجعة" if salvaged else ""
            # ── قصّ حارس الذاكرة: لا يبقى صامتاً في سجلّ ──────────────
            # عطب مقيس (2026-07-26): الحارس امتلأ عند 250,000 فقُصّ ~96,000 منتجاً
            # بلا علم المالك لأشهر — واختفت الشريحة الغالية كلها (أعلى سعر في
            # المفقودات 402.5 ر.س بينما السوق يبلغ 6,199). التحذير كان يُكتب
            # بـlog.warning في ملف لا يُفتح. الآن يُصرَخ به في الواجهة.
            if mstats.get("capped"):
                st.error(
                    f"⛔ **قائمة المفقودات مقصوصة** — بلغ حارس الذاكرة سقفه "
                    f"({int(mstats.get('cap_limit', 0)):,} منتجاً فريداً). احتفظنا "
                    "بالأعلى سعراً ثم الأحدث بترتيب حتمي لحماية الذاكرة، وتجاوزت "
                    f"{int(mstats.get('cap_skipped_rows', 0)):,} ملاحظة إضافية. "
                    "**السقف 250,000 معتمد ولا يُرفع قبل قياس الذاكرة في تشغيل طبيعي.**"
                )
            if astats.get("salvage_error"):
                st.warning(
                    "⚠️ تعذّر الإنقاذ الحتمي للمستبعدات — قد تبقى منتجات قابلة "
                    f"للإنقاذ في «⚪ مستبعد». السبب: {astats['salvage_error']}"
                )
            st.toast(
                f"✅ تحليل مكتمل · 🔴 {counts.get(SectionType.PRICE_RAISE, 0):,} "
                f"· 🟢 {counts.get(SectionType.PRICE_LOWER, 0):,} "
                f"· 🔍 {mstats.get('confirmed_missing', 0):,} مفقود مؤكد"
                f"{recovered_txt}{salvaged_txt}",
                icon="✅",
            )
        except Exception as exc:
            st.error(f"تعذّر التحليل: {exc}")
        state.save(store)

    return _run


_run_v4_post_analysis = run_v4_post_analysis


def _dashboard(state: AppState, store: StateStore, container: Container) -> None:
    dashboard.render(state, on_analyze=_make_analyze(state, store, container))


def _missing(state: AppState, store: StateStore, container: Container) -> None:
    missing.render(
        state, state.missing_df,
        ai_service=container.ai, export_service=container.export,
    )


def _processed(state: AppState, store: StateStore, container: Container) -> None:
    processed.render(state, ai_service=container.ai)


def _excluded_page(state: AppState, store: StateStore, container: Container) -> None:
    """غلاف المستبعد: يمرّر حاوية الاعتماديات لتمكين 🧠 الإحياء بالذكاء الاصطناعي."""
    excluded.render(
        state, state.sections if isinstance(state.sections, dict) else {},
        container=container,
    )


def _redistribute(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة إعادة التوزيع بالذكاء الاصطناعي."""
    redistribute.render(state, container=container)


def _review_page(state: AppState, store: StateStore, container: Container) -> None:
    """قسم تحت المراجعة مع حقن خدمة AI لأزرار الحسم."""
    review.render(
        state, state.sections if isinstance(state.sections, dict) else {},
        ai_service=container.ai,
    )


def _scraper(state: AppState, store: StateStore, container: Container) -> None:
    scraper.render(state, container.scraper)


def _new_at_comp(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة «جديد عند المنافسين» (+ نفذت) من مخزن الكشط مباشرةً."""
    new_at_competitors.render(state, container.scraper)


def _section(page: Any) -> Callable[..., None]:
    """مهايئ موحّد للأقسام السعرية/المراجعة/المستبعد."""

    def _render(state: AppState, store: StateStore, container: Container) -> None:
        # حماية: ضمان تمرير dict (لا None) لمنع انهيار البطاقات أثناء الانتقال.
        page.render(state, state.sections if isinstance(state.sections, dict) else {})

    return _render


def _settings_page(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة الإعدادات."""
    settings.render(state, container=container)


def _trash_bin(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة سلة المحذوفات."""
    trash_bin.render(state, container=container)


def _make_automation(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة أتمتة Make."""
    make_automation.render(state, container=container)


def _smart_automation(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة الأتمتة الذكية."""
    smart_automation.render(state, container=container)


def _product_factory(state: AppState, store: StateStore, container: Container) -> None:
    """صفحة مصنع المنتجات."""
    product_factory.render(state, container=container)


# إخفاء من القائمة فقط — لإعادة أي صفحة احذف اسمها من هنا (لا يحذف الصفحة ولا استيرادها).
HIDDEN_PAGES: frozenset[str] = frozenset({
    "⚡ أتمتة Make",
    "🔄 الأتمتة الذكية",
    "✨ مصنع المنتجات",
})


PAGES: dict[str, Callable[[AppState, StateStore, Container], None]] = {
    "📊 لوحة التحكم": _dashboard,
    "🆕 جديد عند المنافسين": _new_at_comp,
    "🔴 سعر أعلى": _section(price_raise),
    "🟢 سعر أقل": _section(price_lower),
    "✅ موافق عليها": _section(approved),
    "🔍 منتجات مفقودة": _missing,
    "⚠️ تحت المراجعة": _review_page,
    "⚪ مستبعد": _excluded_page,
    "🔄 إعادة التوزيع": _redistribute,
    "✅ تمت المعالجة": _processed,
    "🕷️ كشط المنافسين": _scraper,
    "🎯 رادار الفرص": opportunity_radar.render,
    "⚡ أتمتة Make": _make_automation,
    "🔄 الأتمتة الذكية": _smart_automation,
    "✨ مصنع المنتجات": _product_factory,
    "🗑️ سلة المحذوفات": _trash_bin,
    "⚙️ الإعدادات": _settings_page,
}



_NAV_KEY = "nav_page"  # مفتاح ربط الراديو بـ session_state


def _sync_nav() -> None:
    """on_change callback: يمزامن اختيار القائمة مع AppState فوراً *قبل* Rerun."""
    store = StreamlitStore()
    s = AppState.load(store)
    s.current_page = st.session_state[_NAV_KEY]
    s.save(store)


def _sidebar(state: AppState, container: Container) -> str:
    """شريط جانبي لاختيار القسم (أزرار متوسطة–كبيرة)."""
    from config import APP_VERSION
    from ui.components.page_header import sidebar_nav_css

    st.sidebar.markdown(sidebar_nav_css(), unsafe_allow_html=True)
    st.sidebar.title(f"🧪 مهووس {APP_VERSION}")
    # الإشعارات: قابلة للعرض والمسح (ليست نصاً عالقاً) — تُسرد آخرها مع زرّ تحديد كمقروء.
    try:
        unread = container.notifier.unread_count()
        if unread > 0:
            _sev_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
            with st.sidebar.expander(f"🔔 {unread} إشعار جديد", expanded=False):
                for n in container.notifier.get_unread(limit=8):
                    st.caption(f"{_sev_icon.get(n.severity, '🔵')} **{n.title}** — {n.body}")
                if st.button("✓ تحديد الكل كمقروء", key="_notif_clear",
                             use_container_width=True):
                    container.notifier.mark_all_read()
                    st.rerun()
    except Exception as exc:
        import logging
        logging.getLogger("app").debug("شريط الإشعارات تعذّر عرضه: %s", exc)
    labels = [k for k in PAGES if k not in HIDDEN_PAGES]
    # تطهير التنقل: مفتاح محفوظ يشير لصفحة مُخفاة ⇒ أعِده للافتراضي المحروس (يمنع خطأ الراديو).
    if _NAV_KEY in st.session_state and st.session_state[_NAV_KEY] not in labels:
        st.session_state[_NAV_KEY] = labels[0]
    if state.current_page not in labels:  # لا تُبقِ الحالة مؤشِّرةً لصفحة مخفية
        state.current_page = labels[0]
    # نُهيّئ المفتاح إن لم يكن موجوداً (أول تشغيل / جلسة جديدة).
    if _NAV_KEY not in st.session_state:
        page = state.current_page if state.current_page in labels else labels[0]
        st.session_state[_NAV_KEY] = page
    return st.sidebar.radio(
        "الأقسام", labels, key=_NAV_KEY, on_change=_sync_nav,
        label_visibility="collapsed",
    )


def main() -> None:
    """نقطة الدخول: حمّل الحالة → وزّع على الصفحة → احفظ."""
    # إعادة تعيين أعلام حقن CSS لضمان تطبيق الأنماط عند كل إعادة تشغيل.
    # (session_state يبقى عبر Reruns لكن مخرجات st.markdown تُمحى — فيجب إعادة الحقن.)
    st.session_state.pop("_mhw_cmp_css", None)
    st.session_state.pop("_mhw_hdr_css", None)
    store = StreamlitStore()
    state = AppState.load(store)
    with st.spinner("⏳ جارٍ تحميل آخر نتائج محفوظة..."):
        restored = state.restore_results()  # استعادة آخر تحليل محفوظ عند أول إقلاع للجلسة
    if restored:
        reconcile_state(state)  # طبّق مطابقة الفحص على اللقطة المُستعادة (idempotent)
        # مرجع فحص الماركات من الكتالوج المُستعاد (يُصحّح الفحص فوراً بلا إعادة تحليل)
        try:
            from services.brand_manager import save_store_brands
            save_store_brands(state.our_catalog)
        except Exception as exc:
            import logging
            logging.getLogger("app").debug(
                "تعذّر تحديث مرجع الماركات على الاستعادة: %s", exc,
            )
        state.save(store)
    container = _container()
    choice = _sidebar(state, container)
    state.current_page = choice
    PAGES[choice](state, store, container)
    state.save(store)


if __name__ == "__main__":
    main()
