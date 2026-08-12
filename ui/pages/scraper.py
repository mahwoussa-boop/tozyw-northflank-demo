"""ui/pages/scraper.py — 🕷️ كشط المنافسين (إدارة + كشط + ما تم كشطه، غلاف رفيع).

يعرض المتاجر المُعرَّفة مع **عدد ما كُشط منها وآخر تاريخ كشط**، ويتيح إضافة/حذف
متجر، كشط متجر مفرد، و**كشط الكل** بزرّ واحد. كل المنطق في `ScraperService`.
"""
from __future__ import annotations

from services.scraper_service import Competitor, ScraperService
from ui.state_manager import AppState


# الإضافة تُسجّل المتجر في القائمة فقط ولا تكشطه (`add_competitor` يكتب في ملف
# المتاجر ثم يعود). بلا هذا السطر يظنّ المالك أن البيانات وصلت فينتظر ما لا يأتي.
# الجولة الليلية مهمة MahwousNightlyScrape 03:30 (متحقَّق: Ready · آخر تشغيل بنتيجة 0).
_ADDED_NOT_SCRAPED = (
    "لم تُكشط منتجاته بعد — سيُكشط تلقائياً في الجولة الليلية (٣:٣٠ ص)، "
    "أو اكشطه الآن من صفّه في القائمة أدناه."
)


_DISC_PER_PAGE = 10


def filter_discovered(found: list[dict], known_ids: list[int],
                      min_products: int = 0, query: str = "") -> list[dict]:
    """تصفية المتاجر المكتشفة ووسم المُضاف منها (خالص — بلا Streamlit).

    ثلاث فجوات في لوحة واحدة: متجر بمنتجَين كان يظهر بنفس بروز متجر بـ5,000
    (عتبة)، والقائمة كانت تُرسم كاملة بلا حدّ (ترقيم لاحقاً)، وبعد الإضافة لا
    يتغيّر شكل الصفّ (وسم ``added``).

    البحث يقبل الاسم **أو** المعرّف: أسماء متاجر «محلي» متشابهة، والمعرّف هو
    ما يميّزها فعلاً.
    """
    known = {int(i) for i in known_ids if i}
    q = (query or "").strip().casefold()
    rows = []
    for d in found:
        if int(d.get("product_count") or 0) < int(min_products or 0):
            continue
        sid = int(d.get("store_id") or 0)
        if q and q not in str(d.get("store_name") or "").casefold() and q not in str(sid):
            continue
        rows.append({**d, "added": sid in known})
    return rows


def added_message(name: str, store_id: int, saved: int | None) -> str:
    """رسالة ما بعد إضافة متجر مكتشف — تقول ما حدث فعلاً لا ما نتمنّاه. خالصة.

    ``saved is None`` ⇒ أُضيف بلا كشط (السلوك الافتراضي): تُذكر الجولة الليلية.
    ``saved == 0``    ⇒ كُشط فعلاً ولم يُحفظ شيء — **لا يُقال «نجح»**، فمتجرٌ
    بصفر منتجات إمّا فارغ وإمّا تغيّر عند المصدر، وكلاهما يستحق علم المالك.
    """
    who = f"«{name}» (#{store_id})"
    if saved is None:
        return f"أُضيف {who} — {_ADDED_NOT_SCRAPED}"
    if saved <= 0:
        return (f"أُضيف {who} وكُشط، لكن **لم يُحفظ أي منتج** — "
                "قد يكون المتجر فارغاً أو تغيّر عند المصدر.")
    return f"أُضيف {who} وكُشط فوراً: {saved:,} منتجاً محفوظاً."


def _try_add_discovered(scraper: ScraperService, d: dict):
    """يحاول إضافة متجر مكتشف؛ يُرجع ``(المتجر، None)`` أو ``(None، الخطأ)``.

    الاستثناء يُلتقط هنا لا عند المتصل: في الإضافة الجماعية فشلُ متجر واحد
    (موجود مسبقاً مثلاً) لا يجوز أن يُسقط بقيّة المحدَّدين.
    """
    try:
        return scraper.add_competitor(
            d.get("store_name") or f"متجر {d['store_id']}",
            f"https://mahally.com/stores/{d['store_id']}/",
        ), None
    except Exception as exc:                      # noqa: BLE001 — يُبلَّغ للمالك
        return None, str(exc)


def _scrape_after_add(scraper: ScraperService, comp) -> tuple[int | None, str]:
    """يكشط متجراً أُضيف للتوّ. يُرجع ``(المحفوظ، خطأ)``.

    فشل الكشط **لا يُلغي الإضافة** — المتجر بقي في القائمة وستلتقطه الجولة
    الليلية. لذلك يُعاد الخطأ كتحذير لا كخطأ إضافة.
    """
    try:
        return int(scraper.scrape_and_save(comp)), ""
    except Exception as exc:                      # noqa: BLE001 — كشط لا إضافة
        return None, str(exc)


def _fmt_date(value: str) -> str:
    """تنسيق تاريخ مختصر (YYYY-MM-DD HH:MM) أو «—»."""
    return (value or "")[:16] or "—"


def _render_add(scraper: ScraperService) -> None:
    """نموذج إضافة متجر منافس جديد."""
    import streamlit as st

    with st.popover("➕ إضافة متجر منافس"):
        st.caption("الصق رابط المتجر من محلي، مثل:")
        st.code("https://mahally.com/stores/216339537/")
        url = st.text_input("رابط محلي", key="sc_new_url")
        name = st.text_input("اسم المتجر", key="sc_new_name")
        if st.button("✅ إضافة", key="sc_add"):
            try:
                comp = scraper.add_competitor(name, url)
                st.success(f"أُضيف «{comp.name}» (#{comp.mahally_store_id})")
                st.caption(_ADDED_NOT_SCRAPED)
            except Exception as exc:
                st.error(str(exc))


def _remove_cb(scraper: ScraperService, store_id: int) -> None:
    scraper.remove_competitor(store_id)


def _render_summary(st: object, stats: dict[str, dict]) -> None:
    """ملخّص ما تم كشطه: إجمالي المنتجات + عدد المتاجر + آخر كشط (HTML مدمج لاصق)."""
    from ui.components.page_header import section_accent
    total = sum(int(v.get("count", 0)) for v in stats.values())
    last = max((v.get("last", "") for v in stats.values() if v.get("last")), default="")
    accent = section_accent("scraper")
    st.markdown(
        f'<div class="mhw-ctrl" style="--ac:{accent}">'
        f'<div class="st-row">'
        f'<span class="st-i">📦 <b>{total:,}</b> منتج مكشوط</span>'
        f'<span class="st-i">🏪 <b>{len(stats):,}</b> متجر</span>'
        f'<span class="st-i">🕐 آخر كشط <b>{_fmt_date(last)}</b></span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def _render_scrape_all(st: object, scraper: ScraperService,
                       competitors: list[Competitor]) -> None:
    """زرّ «كشط الكل» مع شريط تقدّم حيّ (يكشط المتاجر التي لها معرّف فقط)."""
    scrapable = [c for c in competitors if c.mahally_store_id]
    if not scrapable:
        return
    if st.button(f"🚀 كشط الكل ({len(scrapable)} متجر)", type="primary",
                 use_container_width=True):
        bar = st.progress(0, text="بدء الكشط…")

        def _cb(i: int, n: int, name: str) -> None:
            bar.progress(min(int(i / max(n, 1) * 100), 100),
                         text=f"كشط «{name}» ({i + 1}/{n})…")

        with st.spinner("جارٍ كشط كل المتاجر…"):
            try:
                results = scraper.scrape_all(_cb)
                bar.progress(100, text="اكتمل")
                saved = sum(v for v in results.values() if v > 0)
                failed = sum(1 for v in results.values() if v < 0)
                st.success(f"✅ حُفظ {saved:,} منتجاً · {failed} متجراً تعذّر كشطه")
            except Exception as exc:
                st.error(f"تعذّر الكشط: {exc}")


def _render_row(scraper: ScraperService, comp: Competitor, idx: int,
                stat: dict) -> None:
    """سطر متجر واحد: ما كُشط منه + آخر تاريخ + كشط + حذف."""
    import streamlit as st

    has_id = bool(comp.mahally_store_id)
    col1, col2, col3 = st.columns([4, 1, 1])
    label = f"🏪 **{comp.name}**"
    if has_id:
        label += f" · `#{comp.mahally_store_id}`"
    # رابط متجر المنافس الأصلي (إن توفّر) — وصول مباشر لمتجره.
    store_url = (comp.store_url or "").strip()
    if store_url.lower().startswith(("http://", "https://")):
        label += f" · [🔗 المتجر]({store_url})"
    col1.markdown(label)
    count = int(stat.get("count", 0))
    if count:
        col1.caption(f"📦 {count:,} منتج · 🕐 آخر كشط {_fmt_date(stat.get('last', ''))}")
    else:
        col1.caption("⚪ لم يُكشط بعد")

    if col2.button("🔄 كشط", key=f"sc_run_{idx}", disabled=not has_id,
                   use_container_width=True):
        with st.spinner(f"جارٍ كشط «{comp.name}»…"):
            try:
                saved = scraper.scrape_and_save(comp)
                run = scraper.last_scrape_run(comp.name)
                if run:
                    new_n = int(run.get("new_count", 0) or 0)
                    oos_n = int(run.get("oos_count", 0) or 0)
                    total_n = int(run.get("total_seen", saved) or saved)
                    updated_n = max(total_n - new_n, 0)
                    st.success(
                        f"✅ «{comp.name}»: 🆕 {new_n:,} جديد · "
                        f"🔄 {updated_n:,} محدّث · 🔴 {oos_n:,} نفذت"
                    )
                else:
                    st.success(f"✅ حُفظ {saved:,} منتجاً من «{comp.name}»")
            except Exception as exc:
                st.error(f"تعذّر الكشط: {exc}")
    col3.button("🗑️ حذف", key=f"sc_rm_{idx}", disabled=not has_id,
                on_click=_remove_cb, args=(scraper, comp.mahally_store_id),
                use_container_width=True)


def _render_unlinked(st: object, unlinked: list[dict]) -> None:
    """قسم المنافسين بلا معرّف محلي (يحتاجون رابط mahally للكشط) — بلون كهرماني مميّز."""
    if not unlinked:
        return
    st.markdown(
        f'<div style="margin:10px 0 4px;color:#F59E0B;font-weight:800;'
        f'font-family:Tajawal,sans-serif;direction:rtl">'
        f'⚠️ {len(unlinked)} منافس بلا معرّف محلي — يحتاجون رابط mahally للكشط الآلي'
        f'</div>',
        unsafe_allow_html=True,
    )
    import html
    rows = ""
    for e in unlinked:
        name = html.escape((e.get("name") or "").strip() or "منافس")
        url = html.escape((e.get("store_url") or "").strip())
        link = (
            f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="color:#F59E0B;text-decoration:none">🔗 {url}</a>'
            if url.lower().startswith(("http://", "https://")) else ""
        )
        rows += (
            f'<div style="display:flex;align-items:center;gap:10px;'
            f'background:#F59E0B14;border:1px solid #F59E0B40;'
            f'border-right:3px solid #F59E0B;border-radius:9px;'
            f'padding:7px 12px;margin:5px 0;direction:rtl">'
            f'<span style="color:#E2E8F0;font-weight:700;flex:1">🏪 {name}</span>'
            f'<span style="font-size:.82rem">{link}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="font-family:Tajawal,sans-serif">{rows}</div>',
        unsafe_allow_html=True,
    )
    st.caption("لتفعيل الكشط الآلي لهذه المتاجر: أضِف رابط mahally.com/stores/ID لكلٍّ منها عبر «➕ إضافة متجر منافس».")


def _render_discovered(st: object, scraper: ScraperService,
                       competitors: list) -> None:
    """قسم «متاجر عطور جديدة مكتشفة» — متاجر في السوق لم نكشطها بعد.

    يقرأ الكاش المخزّن (discovered_stores)، ويتيح إعادة الاكتشاف بزرّ (شبكة)،
    وإضافة متجر بنقرة أو **جماعياً** بعد تصفية (عتبة منتجات + بحث) وترقيم.
    المُضاف يُوسَم «مُضاف ✓» بلا زرّ. آمن: أي غياب/فشل ⇒ لا يظهر شيء يكسر.
    """
    from services.store_discovery_service import (
        discovered_stores_list,
        refresh_discovered_stores,
    )
    from ui.components.pagination import paginate, render_pagination

    with st.expander("🔎 متاجر عطور جديدة في السوق (لم نكشطها بعد)", expanded=False):
        st.caption(
            "متاجر تبيع العطور على «محلي» وليست في قائمتك — مرتّبة بعدد المنتجات. "
            "«اكتشف» يفحص السوق مباشرةً (قد يأخذ ~15 ثانية)."
        )
        if st.button("🔍 اكتشف متاجر جديدة الآن", key="sc_discover"):
            with st.spinner("جاري فحص السوق…"):
                known = [int(getattr(c, "mahally_store_id", 0) or 0) for c in competitors]
                n = refresh_discovered_stores(known)
            st.success(f"اكتُشِف {n} متجراً جديداً" if n else "لم يُكتشف متاجر جديدة الآن")

        found = discovered_stores_list()
        if not found:
            st.info("لا متاجر مكتشفة بعد — اضغط «اكتشف» لفحص السوق.")
            return

        known_ids = [int(getattr(c, "mahally_store_id", 0) or 0) for c in competitors]
        f1, f2 = st.columns([1, 2])
        min_products = f1.number_input("حدّ أدنى للمنتجات", min_value=0, value=0,
                                       step=10, key="sc_disc_min")
        query = f2.text_input("بحث بالاسم أو المعرّف", key="sc_disc_q")
        # الفجوة 1-هـ كاملةً: التنبيه وحده كان يشرح الانتظار، وهذا يُنهيه.
        # افتراضه **مطفأ**: كشط متجر يأخذ ~12 ثانية، فالإضافة الجماعية بكشط
        # قد تمتدّ دقائق — قرارٌ يُتّخذ لا يُفاجَأ به.
        scrape_now = st.checkbox(
            "🔄 اكشطه فور الإضافة (بدل انتظار الجولة الليلية)",
            key="sc_disc_scrape_now",
            help="كشط متجر واحد يأخذ ~12 ثانية. مع «أضِف المحدَّد» يُضرب في عددها.",
        )
        rows = filter_discovered(found, known_ids, int(min_products), query)
        if not rows:
            st.info(f"لا متجر يطابق الفلتر (من {len(found)}) — خفّف الحدّ الأدنى أو امسح البحث.")
            return

        view = paginate(rows, int(st.session_state.get("sc_disc_page", 1)), _DISC_PER_PAGE)
        st.caption(view.caption + (f" · مفلتر من {len(found)}" if len(rows) != len(found) else ""))
        selected = []
        for d in view.items:
            c0, c1, c2 = st.columns([0.4, 4.6, 1])
            rating = (f" · ★{d['rating_avg']:.1f} ({d['rating_count']:,})"
                      if d["rating_count"] > 0 else "")
            if not d["added"] and c0.checkbox(
                    f"تحديد {d['store_id']}", key=f"sc_disc_sel_{d['store_id']}",
                    label_visibility="collapsed"):
                selected.append(d)
            c1.markdown(
                f"**{d['store_name'] or 'متجر #' + str(d['store_id'])}** — "
                f"{d['product_count']:,} منتج عطري{rating}  \n"
                f"<span style='color:#94A3B8;font-size:.78rem'>معرّف: {d['store_id']}</span>",
                unsafe_allow_html=True,
            )
            if d["added"]:
                c2.caption("مُضاف ✓")
            elif c2.button("➕ أضِفه", key=f"sc_disc_add_{d['store_id']}"):
                comp, err = _try_add_discovered(scraper, d)
                if err:
                    st.error(err)
                else:
                    saved, scrape_err = None, ""
                    if scrape_now:
                        with st.spinner(f"جارٍ كشط «{comp.name}»…"):
                            saved, scrape_err = _scrape_after_add(scraper, comp)
                    st.success(added_message(comp.name, comp.mahally_store_id, saved))
                    if scrape_err:
                        st.warning(f"أُضيف المتجر لكن تعذّر كشطه الآن: {scrape_err}")
        render_pagination(view, "sc_disc")

        if selected and st.button(f"➕ أضِف المحدَّد ({len(selected)})",
                                  key="sc_disc_bulk", type="primary"):
            done, failed, scraped = 0, [], 0
            bar = st.progress(0, text="جارٍ الإضافة…") if scrape_now else None
            for i, d in enumerate(selected):
                comp, err = _try_add_discovered(scraper, d)
                if err:
                    failed.append(f"#{d['store_id']}: {err}")
                    continue
                done += 1
                if scrape_now:
                    bar.progress(int((i + 1) / len(selected) * 100),
                                 text=f"كشط «{comp.name}» ({i + 1}/{len(selected)})…")
                    saved, scrape_err = _scrape_after_add(scraper, comp)
                    if scrape_err:
                        failed.append(f"#{d['store_id']}: أُضيف لكن تعذّر كشطه — {scrape_err}")
                    else:
                        scraped += int(saved or 0)
            if bar is not None:
                bar.progress(100, text="اكتمل")
            if done:
                st.success(f"أُضيفت {done} متاجر" + (
                    f" · كُشط منها {scraped:,} منتجاً" if scrape_now
                    else f" — {_ADDED_NOT_SCRAPED}"))
            for msg in failed:
                st.warning(msg)


def render(state: AppState, scraper: ScraperService) -> None:
    """يعرض صفحة كشط المنافسين كاملة (ملخّص + كشط الكل + متاجر بما كُشط منها)."""
    import streamlit as st

    from ui.components.page_header import render_page_header

    competitors = scraper.list_competitors()
    render_page_header("كشط المنافسين", section="scraper", count=len(competitors))
    stats = scraper.scraped_stats()
    if stats:
        _render_summary(st, stats)
    col_add, col_all = st.columns([1, 2])
    with col_add:
        _render_add(scraper)
    with col_all:
        _render_scrape_all(st, scraper, competitors)

    # ── متاجر عطور جديدة مكتشفة في السوق (لم نكشطها بعد) ──
    _render_discovered(st, scraper, competitors)

    st.divider()
    if not competitors:
        st.info("لا متاجر مُعرَّفة بمعرّف محلي — أضِف متجراً عبر الزر أعلاه")
    for idx, comp in enumerate(competitors):
        _render_row(scraper, comp, idx, stats.get(comp.name, {}))

    # ── المنافسون بلا معرّف محلي (مميَّزون بلون كهرماني) ──
    _render_unlinked(st, scraper.list_unlinked())
