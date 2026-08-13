"""ui/pages/new_at_competitors.py — 🆕 جديد عند المنافسين (+ 🔴 نفذت) — موحّد.

يستهلك طبقة الملكية (``ownership_service``): يجمّع منتجات المنافسين حسب المنتج
(متعدد المنافسين)، ويقرّر «هل نملكه؟» (OWNED/REVIEW/MISSING)، ويعرضها ببطاقة
الفرصة المشتركة (``build_missing_card_html`` بمعامل ``kind``) — فيُحذف التكرار
ويُصلَح العرض. في «نفذت»: لمنتجاتنا (OWNED) زرّ تصفير/تحديث المخزون عبر Make
(``WEBHOOK_UPDATE_QUANTITY``). 0 = «نفذ لدينا».
"""
from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd

from services.export_service import ExportService, to_quantity_payload
from services.ownership_service import (
    KEY_OUR_MATCH,
    KEY_OUR_PRODUCT_ID,
    KEY_OWNERSHIP,
    build_ownership_matcher_cached,
    prepare_competitor_feed,
)
from services.scraper_service import ScraperService
from ui.components.comparison_card import build_missing_card_html, comparison_card_css
from ui.components.page_header import render_page_header, section_accent
from ui.components.pagination import paginate, render_pagination
from ui.state_manager import AppState, row_image_url

_NEW = "#10B981"   # لون «جديد»
_OOS = "#EF4444"   # لون «نفذت»

_PERIODS = {
    "🆕 منذ آخر كشطة": None,
    "آخر 3 أيام": 3,
    "آخر 7 أيام": 7,
    "آخر 14 يوم": 14,
    "آخر شهر": 30,
}


def _ctrl_bar(n_new: int, n_oos: int) -> str:
    accent = section_accent("new_at_comp")
    return (
        f'<div class="mhw-ctrl" style="--ac:{accent}">'
        '<div class="st-row">'
        f'<span class="st-i">🆕 <b>{n_new:,}</b> جديد</span>'
        f'<span class="st-i">🔴 <b>{n_oos:,}</b> نفذت</span>'
        '</div></div>'
    )


# ── إجراء تحديث/تصفير مخزون منتجنا (عبر سيناريو الكمية المنفصل) ─────────────
def _send_quantity(st: Any, product_id: str, name: str, quantity: int) -> None:
    """يرسل تحديث كمية منتجنا إلى Make (``WEBHOOK_UPDATE_QUANTITY``). 0 = نفذ لدينا.

    إرسال فقط + تغذية راجعة واضحة (لا يمسّ السعر ولا حالة المعالجة).
    """
    url = os.environ.get("WEBHOOK_UPDATE_QUANTITY", "")
    if not url:
        st.error("⚠️ رابط Webhook الكمية غير مهيّأ (WEBHOOK_UPDATE_QUANTITY)")
        return
    row = {"No.": product_id, "product_id": product_id, "أسم المنتج": name}
    items = to_quantity_payload(pd.DataFrame([row]), quantity)
    if not items or not items[0].get("NO"):
        st.error("⚠️ معرّف المنتج (Salla ID) مفقود — تعذّر التحديث")
        return
    try:
        with st.spinner("جاري تحديث المخزون…"):
            result = ExportService().post_to_make(url, items)
    except Exception as exc:
        st.error(f"❌ خطأ في الاتصال بـ Make: {exc}")
        return
    if result.get("success"):
        label = "نفذ (الكمية = 0)" if quantity == 0 else f"الكمية = {quantity}"
        st.success(f"✅ أُرسل تحديث المخزون: {label}")
    else:
        body = result.get("body") or result.get("error") or ""
        st.error(f"❌ فشل الإرسال (HTTP {result.get('status_code', '?')}): {body}")


def _render_zero_quantity(st: Any, rowd: dict, wkey: str) -> None:
    """موسّع تحديث/تصفير مخزون منتجنا (يظهر في «نفذت» للمنتجات التي نملكها)."""
    pid = str(rowd.get(KEY_OUR_PRODUCT_ID) or "")
    name = str(rowd.get(KEY_OUR_MATCH) or rowd.get("منتج_المنافس") or "")
    with st.expander("📦 تحديث مخزوننا (Make)", expanded=False):
        qty = st.number_input(
            "الكمية", min_value=0, step=1, value=0, key=f"q_{wkey}",
            help="0 = نفذ لدينا (يُخفى من متجرنا). مستقل عن السعر.",
        )
        ok = st.checkbox("أؤكّد تحديث المخزون", key=f"ok_{wkey}",
                         help="حماية من التصفير العرضي")
        if st.button("📦 تحديث المخزون (Make)", key=f"b_{wkey}",
                     disabled=not ok, use_container_width=True):
            _send_quantity(st, pid, name, int(qty))


# ── إجراءات بطاقة «جديد»: تجهيز وإرسال Make / إخفاء (مثل صفحة المفقودات) ────
def _is_actioned(state: AppState, rowd: dict) -> bool:
    """هل عُولج هذا المنتج (أُرسِل أو أُخفي)؟ — لإسقاطه من «جديد المنافسين» بعد الإجراء."""
    name = str(rowd.get("منتج_المنافس") or "").strip()
    if name and state.is_hidden(name):
        return True
    from ui.pages.missing import missing_row_key
    key = missing_row_key(rowd)
    return bool(key) and key in state.processed_missing_urls


def _enrich_and_send_single(st: Any, state: AppState, rowd: dict, wkey: str,
                            *, force: bool = False) -> None:
    """يُثري منتج منافس (غير مملوك) تلقائياً ثم يرسله إلى Make.com.

    يبني صفّاً واحداً بمفاتيح المحرك ويضبط ``مستوى_الثقة=green`` (مؤكَّد: موجود عند
    المنافس وغير مملوك لدينا)، ثم يمرّره عبر ``MissingProductsOrchestrator``
    (إثراء + بوابة جودة + بوابة منع التكرار + إرسال). يعيد استخدام مسار المفقودات.
    ``force=True`` يتخطّى بوابة منع التكرار (إرسال «رغم التشابه» بقرار المستخدم).
    """
    import os

    name = str(rowd.get("منتج_المنافس") or "").strip()
    price_val = st.session_state.get(f"nac_price_{wkey}")
    try:
        price = float(price_val) if price_val else 0.0
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        st.warning("⚠️ السعر غير صالح — عدّل السعر أولاً")
        return
    if not os.environ.get("WEBHOOK_NEW_PRODUCTS", ""):
        st.error("⚠️ رابط Webhook غير مهيّأ (.env)")
        return

    with st.spinner(f"⏳ تجهيز «{name[:40]}» (إثراء + وصف + إرسال)…"):
        try:
            from services.missing_orchestrator import MissingProductsOrchestrator
            row = dict(rowd)
            row["سعر_المنافس"] = price
            row["السعر_المقترح"] = price
            row["مستوى_الثقة"] = "green"   # مؤكَّد مفقود (عند المنافس وغير مملوك)
            orch = MissingProductsOrchestrator(
                our_catalog_df=getattr(state, "our_catalog", None),
                enrich_with_ai=True,
                skip_dedup=force,     # ← إرسال «رغم التشابه» عند طلب المستخدم
            )
            report = orch.process_dataframe(pd.DataFrame([row]), export_target="make")
        except Exception as exc:
            st.error(f"❌ فشل التجهيز: {exc}")
            return

    res = report.export_result
    sent = int(res.get("sent", res.get("count", 0)) or 0)
    if res.get("success") and sent > 0:
        st.success(f"✅ أُرسل «{name[:40]}» إلى Make — تحقّق من سلة للتأكيد (200 = استلام لا إنشاء)")
        st.session_state.pop(f"_nac_held_{wkey}", None)
        from ui.pages.missing import missing_row_key
        key = missing_row_key(row)
        if key:
            state.mark_missing_processed(key)
            state.log_action(
                key=key, name=name, action="تجهيز وإرسال (جديد المنافسين)",
                detail=f"إثراء تلقائي + إرسال بسعر {price:,.0f} ر.س ← Make",
                kind="missing", image=row_image_url(row),
            )
            state.persist_results()
        st.rerun()
    elif res.get("success") and sent == 0:
        # نجاح بلا إرسال فعلي = احتجزته بوابة منع التكرار — أتِح تجاوزها يدوياً.
        d = res.get("dedup", {})
        st.session_state[f"_nac_held_{wkey}"] = True
        st.warning(
            f"⚠️ لم يصل «{name[:40]}» — احتجزته بوابة منع التكرار "
            f"(مطابق {d.get('match', 0)} · محتمل {d.get('likely', 0)}). إن كان "
            f"منتجاً مختلفاً فعلاً (تركيز/حجم) فاضغط «🚀 أرسله رغم التشابه»."
        )
    else:
        errors = report.errors or []
        msg = res.get("message", "خطأ غير معروف")
        st.error(f"❌ {errors[0] if errors else msg}")


def _hide_product(st: Any, state: AppState, rowd: dict) -> None:
    """يُخفي منتجاً من «جديد المنافسين» (حذف ناعم قابل للتراجع من «تمت المعالجة»)."""
    name = str(rowd.get("منتج_المنافس") or "").strip()
    if not name:
        return
    state.hide(name)
    state.log_action(
        key=name, name=name, action="تجاهل/إخفاء",
        detail="أُزيل من «جديد المنافسين»", kind="hidden",
        image=row_image_url(rowd),
    )
    state.persist_results()
    st.rerun()


def _render_new_actions(st: Any, state: AppState, rowd: dict, wkey: str) -> None:
    """أزرار إجراء أسفل بطاقة «جديد»: سعر + تجهيز وإرسال Make (لغير المملوك) + إخفاء."""
    owned = rowd.get(KEY_OWNERSHIP) == "owned"
    try:
        comp_price = float(rowd.get("سعر_المنافس", 0) or 0)
    except (TypeError, ValueError):
        comp_price = 0.0
    suggested = max(comp_price - 1, 0.0) if comp_price > 0 else 0.0

    c_price, c_send, c_hide = st.columns([2.5, 3.5, 1.5])
    with c_price:
        st.number_input(
            "💰 السعر (ر.س)", min_value=0.0, value=float(suggested), step=1.0,
            key=f"nac_price_{wkey}",
            help="السعر المقترح = سعر المنافس − 1. عدّل حسب الحاجة.",
        )
    with c_send:
        if owned:
            st.success("✅ تملكه — لا إضافة")
        else:
            if st.button("🚀 تجهيز وإرسال Make", key=f"nac_send_{wkey}",
                         type="primary", use_container_width=True):
                _enrich_and_send_single(st, state, rowd, wkey)
            # ظهر فقط بعد احتجاز بوابة التكرار: إرسال متعمّد رغم التشابه.
            if st.session_state.get(f"_nac_held_{wkey}") and st.button(
                "🚀 أرسله رغم التشابه", key=f"nac_sendf_{wkey}", use_container_width=True,
                help="يتخطّى بوابة منع التكرار — للنُّسخ المختلفة (تركيز/حجم)",
            ):
                _enrich_and_send_single(st, state, rowd, wkey, force=True)
    with c_hide:
        if st.button("❌ تجاهل", key=f"nac_hide_{wkey}", use_container_width=True,
                     help="تجاهل / إخفاء هذا المنتج"):
            _hide_product(st, state, rowd)


_matcher_cache: Any = None  # كاش كسول (هويّة ثابتة عبر التشغيلات — نمط _get_stockout_cache في dashboard.py)


def catalog_signature() -> str:
    """بصمة رخيصة للكتالوج تُبطل كاش المُطابِق عند تغيّره **فعلاً** (خالص).

    قياس 2026-07-25: البصمة ~9م.ث مقابل بناء المُطابِق **1,151م.ث دافئاً و5,275
    بارداً** ⇒ 130×. لماذا بديلاً عن ``ttl`` الأعمى: TTL يُسقط الكاش كل 30 دقيقة
    وإن لم يتغيّر شيء (فيدفع المالك الكلفة كل نصف ساعة سكون)، **ويتأخّر مع ذلك
    حتى 30 دقيقة** عن كتالوج تغيّر بعد تحليل. البصمة تُصلح الاتجاهين معاً.

    ما تكتشفه: إضافة/حذف صفوف (``COUNT``/``MAX(rowid)``) · تغيّر الأسماء
    (مجموع أطوال ``norm_name`` و``product_name``) · مزامنة كتالوج (``last_seen``،
    دقّته يوم) · **وأي تحليل** عبر ``run_id`` — وهو الإشارة الوحيدة التي تعبر
    العمليات، لأن المشغّل الخلفي عملية منفصلة لا تستطيع إبطال كاش الخادم.

    الفشل يُرجع ثابتاً (``?``) لا قيمة متغيّرة: إسقاط الكاش عند تعذّر القراءة
    يحوّل عطباً في البصمة إلى بطء دائم. الـ``ttl`` الباقي شبكة أمان أخيرة.
    """
    import sqlite3

    from services.analysis_runner import read_progress
    from utils.data_paths import get_data_db_path

    table = "?"
    try:
        con = sqlite3.connect(
            f"file:{get_data_db_path('pricing_v18.db')}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT COUNT(*), MAX(rowid), SUM(LENGTH(norm_name)), "
                "SUM(LENGTH(product_name)), MAX(last_seen) FROM our_catalog"
            ).fetchone()
            table = "-".join(str(v) for v in row)
        finally:
            con.close()
    except Exception:                       # noqa: BLE001 — بصمة لا تكسر صفحة
        pass
    run_id = str((read_progress() or {}).get("run_id") or "")
    return f"{table}|{run_id}"


_SIG_TTL_SEC = 60.0
_sig_memo: tuple[float, str] = (0.0, "")


def _cached_signature() -> str:
    """يحسب البصمة مرّة كل 60 ثانية لا مرّة كل رسم — بمذكّرة عادية لا كاش Streamlit.

    قياس معزول داخل عملية واحدة (2026-07-25) بثلاث محاولات:
    | التهيئة | الصفحة (وسيط) | صافي الكلفة |
    |---|---|---|
    | بلا بصمة (الأصل) | ~80-89م.ث | — |
    | بصمة عند كل رسم | ~103م.ث | +17م.ث |
    | بصمة داخل ``st.cache_data(ttl=60)`` | ~109م.ث | **+29م.ث** |

    ``st.cache_data`` **أغلى من الاستعلام نفسه** (تجزئة المفتاح + تسلسل النتيجة)،
    فطبقة «التسريع» كانت تُبطئ. المذكّرة الزمنية العادية تُكلّف ميكروثوانٍ.

    لماذا 60 ثانية أصلاً: الصفحة تُعاد رسمها عند **كل** تفاعل (ترقيم · فلتر ·
    إخفاء)، فجلسة تصفّح بستّين تفاعلاً كانت ستدفع ~1.0 ثانية — أي ما يقارب كلفة
    إعادة البناء التي نتفاداها (1,151م.ث)، فيصير الكسب وهمياً.

    النطاق عمليةٌ كاملة كـ``cache_resource`` (متغيّر وحدة لا ``session_state``)،
    وتأخّر الكشف 60 ثانية مقابل 30 دقيقة قبل هذا التغيير.
    """
    global _sig_memo
    now = time.monotonic()
    if _sig_memo[1] and (now - _sig_memo[0]) < _SIG_TTL_SEC:
        return _sig_memo[1]
    sig = catalog_signature()
    _sig_memo = (now, sig)
    return sig


def _matcher(st: Any):
    """يبني مُطابِق الملكية من كتالوجنا — مشترك بين كل الجلسات، ويُبنى عند التغيّر.

    هـ1 (قياس فتح الصفحات 2026-07-22): الكاش السابق بـ``session_state`` كان يعيد
    البناء (~6-13 ثانية) عند كل جلسة متصفّح جديدة رغم ثبات الكتالوج غالباً؛
    ``st.cache_resource`` يشارك النتيجة بين كل الجلسات على الخادم — لا يمسّ
    ``MatchingService``/منطق المطابقة نفسه، فقط نطاق تخزين نتيجته.

    2026-07-25: المفتاح صار بصمة الكتالوج بدل الاعتماد على انتهاء زمني وحده
    (انظر ``catalog_signature``). ``max_entries=2`` يمنع تراكم مُطابِقات قديمة
    في الذاكرة عند تغيّر البصمة — كل مُطابِق يحمل 11,566 عنصراً.
    """
    global _matcher_cache
    if _matcher_cache is None:
        @st.cache_resource(ttl=21600, max_entries=2,
                           show_spinner="تحضير مطابقة الكتالوج…")
        def _build(_sig: str):
            # عبر المذكّرة على مستوى العملية لا البناء المباشر: خيط التسخين
            # (services/warmup.py) يكون قد ملأها عند الإقلاع، فأول فتح بعد
            # إعادة تشغيل الخادم لا يدفع الـ5,941م.ث المقيسة.
            return build_ownership_matcher_cached(_sig)

        _matcher_cache = _build
    return _matcher_cache(_cached_signature())


def _feed(st: Any, mode: str, sig: str, rows: list, matcher) -> list:
    """يحسب التغذية (تجميع + ملكية) مرّة لكل فلتر — يُخزّن لتفادي إعادة الحساب عند التصفّح."""
    cached = st.session_state.get("_nac_feed")
    if cached is not None and cached[0] == sig:
        return cached[1]
    matching, name_to_id = matcher
    with st.spinner("تحليل منتجات المنافسين…"):
        data = prepare_competitor_feed(rows, matching, name_to_id)
    st.session_state["_nac_feed"] = (sig, data)
    return data


# ── فلتر الملكية (طبقة عرض/تصفية فقط على feed المحسوب أصلاً) ──────────────────
_OWNERSHIP_FILTER_OPTIONS = ["missing", "review", "owned", ""]  # "" = الكل
_OWNERSHIP_FILTER_LABELS = {
    "missing": "ليست في متجرك", "review": "محتمل", "owned": "لديك", "": "الكل",
}


def filter_feed_by_ownership(feed: list, ownership: str) -> list:
    """يرشّح feed حسب ``KEY_OWNERSHIP``. ``""`` ⇒ الكل. خالص (لا يعدّل feed).

    صفوف بلا قرار ملكية تظهر في «الكل» فقط (لا تُطابِق فئة محدّدة).
    """
    if not ownership:
        return list(feed)
    return [
        r for r in feed
        if (r.get(KEY_OWNERSHIP) if hasattr(r, "get") else None) == ownership
    ]


def _ownership_counts(feed: list) -> dict:
    """عدّاد ``{missing, review, owned}`` من feed. خالص."""
    counts = {"missing": 0, "review": 0, "owned": 0}
    for r in feed:
        o = r.get(KEY_OWNERSHIP) if hasattr(r, "get") else None
        if o in counts:
            counts[o] += 1
    return counts


def _feed_has_ownership(feed: list) -> bool:
    """هل يحمل أي صفّ قرار ملكية؟ (وإلا نعرض الكل بلا فلتر — لا كسر)."""
    return any(hasattr(r, "get") and r.get(KEY_OWNERSHIP) for r in feed)


def _apply_ownership_filter(st: Any, feed: list) -> list:
    """يعرض عنصر تصفية الملكية ويرشّح feed. مطوَّق تماماً: أي خطأ أو غياب قرارات
    ملكية ⇒ يُعيد feed كما هو (يظهر الكل، لا كسر). لا يمسّ بيانات ولا منطق إرسال."""
    try:
        if not _feed_has_ownership(feed):
            return feed
        counts = _ownership_counts(feed)
        total = len(feed)

        def _fmt(v: str) -> str:
            if v == "":
                return f"الكل ({total})"
            return f"{_OWNERSHIP_FILTER_LABELS[v]} ({counts.get(v, 0)})"

        selected = st.radio(
            "عرض حسب الملكية", _OWNERSHIP_FILTER_OPTIONS, index=0,
            format_func=_fmt, horizontal=True,
            key="nac_own_filter", label_visibility="collapsed",
        )
        return filter_feed_by_ownership(feed, selected)
    except Exception:
        return feed


def render(state: AppState, scraper: ScraperService) -> None:
    """يعرض «جديد عند المنافسين» موحّداً (تجميع + ملكية + بطاقة مشتركة)."""
    import streamlit as st

    stores = scraper.competitor_names()
    render_page_header("جديد عند المنافسين", section="new_at_comp")
    if scraper.query_error:
        st.error(scraper.query_error)
        return
    if not stores:
        st.info(
            "لا توجد بيانات كشط بعد. اكشط متجراً من «🕷️ كشط المنافسين» "
            "ثم ستظهر هنا المنتجات الجديدة والتي نفذت."
        )
        return

    # ── الفلاتر ──
    f1, f2, f3 = st.columns([2, 2, 2])
    view = f1.radio("العرض", ["🆕 جديد", "🔴 نفذت"], horizontal=True,
                    key="nac_view", label_visibility="collapsed")
    comp_choice = f2.selectbox("🏬 المنافس", ["الكل"] + stores, key="nac_comp")
    competitor = None if comp_choice == "الكل" else comp_choice
    mode = "oos" if view.startswith("🔴") else "new"

    if mode == "new":
        period_label = f3.selectbox("📅 الفترة", list(_PERIODS.keys()), key="nac_period")
        since_days = _PERIODS[period_label]
        if since_days is None:
            # هـ1-تابع: فرع «منذ آخر كشطة» الافتراضي كان JOIN حيّاً ثقيلاً (~9-12s)
            # على 430k صف؛ يُقرأ الآن من كاش مُعاد بناؤه وقت الكشط (رجوع آمن للحيّ
            # إن غاب الكاش — انظر new_products_cached).
            rows = scraper.new_products_cached(competitor=competitor, limit=10000)
        else:
            rows = scraper.new_products(competitor=competitor, since_days=since_days, limit=10000)
    else:
        period_label = ""
        rows = scraper.out_of_stock(competitor=competitor, limit=10000)

    if scraper.query_error:
        st.error(scraper.query_error)
        return

    # ── عدّادات الشريط (تعكس فلتر المنافس) ──
    # OOS: عدّاد صادق بـ COUNT مباشر — العدد الحقيقي (لا يُقتطع عند 10000) وبلا
    # materialize ثقيل. (عدّاد «الجديد» يبقى len لأن كلفته الاستعلامُ الفرعي المرتبط
    # الذي يُعالَج في كتلة لاحقة — تحويله لـ COUNT لا يسرّعه.)
    n_oos = scraper.out_of_stock_count(competitor=competitor)
    if mode == "new":
        n_new = len(rows)
    else:
        n_new = len(scraper.new_products(competitor=competitor, limit=10000))
    if scraper.query_error:
        st.error(scraper.query_error)
        return
    st.markdown(_ctrl_bar(n_new, n_oos), unsafe_allow_html=True)

    # CSS البطاقة المُثبَتة (كل تشغيلة — يضمن العرض الصحيح RTL)
    st.markdown(comparison_card_css(), unsafe_allow_html=True)

    if not rows:
        st.info("✅ لا منتجات نافذة حالياً." if mode == "oos"
                else "لا منتجات جديدة ضمن هذا الفلتر.")
        return

    # ── الموحّد: تجميع متعدد المنافسين + قرار الملكية (مُخزّن لكل فلتر) ──
    sig = f"{mode}|{competitor}|{period_label}|{len(rows)}"
    feed = _feed(st, mode, sig, rows, _matcher(st))
    # إسقاط ما عُولج (أُرسِل/أُخفي) — يُحسب لكل تشغيلة من الحالة الحيّة (لا يُخزَّن مع التغذية).
    feed = [r for r in feed if not _is_actioned(state, r)]
    if not feed:
        st.info("لا منتجات لعرضها بعد التجميع.")
        return

    # ── فلتر الملكية (وضع «جديد»: يرى المالك «غير متوفّرة» افتراضياً = ما يستحقّ الإضافة) ──
    if mode == "new":
        feed = _apply_ownership_filter(st, feed)
        if not feed:
            st.info("لا منتجات في هذه الفئة — بدّل الفلتر أعلاه.")
            return

    df = pd.DataFrame(feed)
    page = int(st.session_state.get("nac_page", 1))
    view_page = paginate(df, page, per_page=12)
    st.caption(view_page.caption)

    accent = _OOS if mode == "oos" else _NEW
    from services.top_competitor_service import badge_for_name
    from services.store_profile_service import store_badge_for_name
    for _idx, row in view_page.items.iterrows():
        rowd = row.to_dict()
        st.markdown(
            build_missing_card_html(rowd, kind=mode, accent=accent),
            unsafe_allow_html=True,
        )
        # وسم الملف التقييمي للمنتج (⭐ تقييم المنتج بالسوق) — عرض فقط، فارغ آمن.
        _rate = badge_for_name(str(rowd.get("منتج_المنافس") or ""))
        if _rate:
            st.caption(_rate)
        # وسم تقييم المتجر (🏪 تحت اسم المتجر) — عرض فقط، فارغ آمن.
        _store = store_badge_for_name(str(rowd.get("المنافس") or ""))
        if _store:
            st.caption(_store)
        if mode == "new":
            # «جديد»: أزرار تجهيز وإرسال Make (لغير المملوك) + إخفاء — لكل بطاقة.
            # مفتاح فريد قطعاً (الفهرس + الاسم) يمنع تصادم مفاتيح Streamlit.
            wkey = f"{_idx}_{str(rowd.get('منتج_المنافس') or '')[:40]}"
            _render_new_actions(st, state, rowd, wkey)
        # منتجاتنا في «نفذت» ⇒ زرّ تصفير/تحديث المخزون
        elif (mode == "oos"
                and rowd.get(KEY_OWNERSHIP) == "owned"
                and rowd.get(KEY_OUR_PRODUCT_ID)):
            _render_zero_quantity(st, rowd, f"{_idx}_{rowd.get(KEY_OUR_PRODUCT_ID)}")

    render_pagination(view_page, "nac")
