"""ui/pages/opportunity_radar.py — 🎯 رادار الفرص (عرض فقط)

صفحة عرض خالصة تستهلك ``services.opportunity_service`` (لا تكرّر منطق الدرجة ولا
الـSQL — تستورده). تعرض أعلى المنتجات نفاداً/طلباً لدى المنافسين لالتقاطها.

القراءة عبر الخدمة (``mode=ro``). الكتابة الوحيدة: سطرُ قرارٍ إلحاقيّ في
``price_history`` عند «موافقة/تعديل» (``append_price_decision``) — لا إرسال خارجيّ
إطلاقاً (كتابة في القاعدة فقط) ولا لمس our_catalog/التسعير/المطابقة. أي فلتر عرضٌ فقط.
"""
from __future__ import annotations

from typing import Any

from services.opportunity_service import (
    MIN_STORE_COUNT,
    format_data_age,
    latest_data_age_days,
    top_opportunities,
)
from ui.components.page_header import render_page_header

_DEFAULT_TOP_N = 25

# أنماط تدلّ على «عيّنة/تستر» صغيرة الحجم — لفلتر العرض الاختياري فقط (لا يمسّ المحرّك).
_SAMPLE_HINTS = ("عينة", "عيّنة", "عينه", "تستر", "1.5مل", "1.2مل", "2مل")


def _is_sample(norm_name: str) -> bool:
    """هل الاسم يبدو عيّنة/تستر صغيرة؟ (تصفية عرض فقط)."""
    n = norm_name or ""
    return any(hint in n for hint in _SAMPLE_HINTS)


def _last_decisions() -> dict:
    """آخر قرار مسجَّل لكل منتج من price_history (قراءة فقط، mode=ro).

    يعيد ``{product_name: (decision, our_price, date)}``؛ ``{}`` عند غياب
    البيانات/الجدول — لا يكسر الصفحة. (لا يكتب شيئاً.)
    """
    import sqlite3
    try:
        from utils.data_paths import get_data_db_path
        path = get_data_db_path("pricing_v18.db")
        conn = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT product_name, decision, our_price, date FROM price_history "
                "WHERE id IN (SELECT MAX(id) FROM price_history GROUP BY product_name)"
            )
            return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        return {}


def _radar_badge_text(row: dict) -> str:
    """سطر شارات الرادار من المفاتيح الإضافية للجسر (عرض فقط، نص عربي)."""
    score = row.get("score") or 0.0
    out = row.get("out_ratio") or 0.0
    sug = row.get("السعر_المقترح")
    sug_txt = f"{sug:g} ر.س" if isinstance(sug, (int, float)) else "—"
    return f"🎯 درجة {score:.0%} · 📉 نفاد {out:.0%} · 💡 مقترح {sug_txt}"


def _render_opportunity_cards(st: Any, opps: list) -> None:
    """يعرض الفرص كبطاقات عبر الجسر + المكوّن — تطويق **مستقل لكل صف**. عرض فقط.

    فشل صفٍّ واحد ⇒ سطر بديل بسيط له وحده (لا انهيار القسم ولا الصفحة).
    """
    from services.opportunity_service import opportunity_to_card_row
    from ui.components.comparison_card import render_missing_card
    for o in opps:
        try:
            row = opportunity_to_card_row(o)
            render_missing_card(row)
            st.caption(_radar_badge_text(row))
        except Exception:
            st.caption(f"⚠️ تعذّر عرض بطاقة: {getattr(o, 'norm_name', '')[:52]}")


def render(state: Any = None, store: Any = None, container: Any = None) -> None:
    """يعرض "رادار الفرص" (قراءة فقط). التوقيع يطابق نوع ``PAGES`` في app.py."""
    import pandas as pd
    import streamlit as st

    render_page_header("رادار الفرص")
    st.caption(
        "منتجات نفَذت لدى أكثر المنافسين ولدينا فرصة التقاطها — مرتّبة بدرجة "
        "أهمية شفّافة (نفاد المخزون + انتشار عبر المتاجر + تقييم العملاء)."
    )
    # طزاجة البيانات: أحدث كشط منافس عبر القاعدة (لا اقتراح سعر من بيانات قديمة).
    _latest_age = latest_data_age_days()
    if _latest_age is not None:
        st.caption(f"📅 أحدث بيانات المنافسين: {format_data_age(_latest_age)}")

    # ── ضوابط العرض (تصفية/عدد — لا تمسّ المحرّك ولا الدرجة) ──
    c1, c2 = st.columns([2, 3])
    top_n = c1.slider(
        "عدد المنتجات", min_value=5, max_value=100, value=_DEFAULT_TOP_N,
        step=5, key="opp_top_n",
    )
    hide_samples = c2.checkbox(
        "إخفاء العيّنات والتستر الصغيرة", value=True, key="opp_hide_samples",
        help="فلتر عرض فقط: يُخفي أسماء تحوي «عينة/تستر» أو أحجام 1.5/2مل.",
    )

    # ── جلب من الخدمة (قراءة فقط) مع تطويق كامل — لا تُكسر الصفحة عند غياب البيانات ──
    try:
        # نجلب فائضاً قبل فلتر العرض كي يبقى العدد المطلوب مكتملاً بعد الإخفاء.
        fetch_n = top_n * 4 if hide_samples else top_n
        opps = top_opportunities(limit=fetch_n)
    except Exception as exc:  # قاعدة مفقودة/تالفة/بلا جدول — رسالة عربية واضحة لا انهيار
        st.warning(
            "تعذّر قراءة بيانات الفرص من قاعدة المنافسين حالياً. "
            "اكشط متجراً من «🕷️ كشط المنافسين» ثم عُد لهذه الصفحة."
        )
        st.caption(f"التفصيل التقني: {exc}")
        return

    if hide_samples:
        opps = [o for o in opps if not _is_sample(o.norm_name)]
    opps = opps[:top_n]

    if not opps:
        st.info(
            "لا فرص لعرضها بعد. اكشط المنافسين من «🕷️ كشط المنافسين» "
            "أو خفّف فلتر العيّنات أعلاه."
        )
        return

    st.caption(f"أعلى {len(opps)} فرصة · عتبة الظهور ≥ {MIN_STORE_COUNT} متاجر.")
    df = pd.DataFrame([
        {
            "المنتج": o.norm_name,
            "آخر تحديث": format_data_age(o.data_age_days),
            "متاجر": o.store_count,
            "% نفَذ": round(100 * o.out_ratio),
            "أدنى": o.price_min,
            "وسيط": o.price_median,
            "أعلى": o.price_max,
            "السعر المقترَح": (
                "قديمة — حدّث الكشط" if o.stale_reason == "stale"
                else (o.suggested_price if o.suggested_price is not None else "—")
            ),
            "التقييم": round(o.rating_avg, 2) if o.rating_avg is not None else None,
            "لدينا": "نعم" if o.in_our_catalog else "لا",
        }
        for o in opps
    ])
    st.dataframe(df, hide_index=True, width="stretch")

    # ── عرض اختياري كبطاقات (كسول: يُبنى فقط عند التفعيل) — الجدول يبقى الافتراضي ──
    if st.toggle("🃏 عرض كبطاقات", key="opp_cards",
                 help="يعرض نفس الفرص كبطاقات بصرية أسفل الجدول (شارات: درجة · نفاد · مقترح)."):
        _render_opportunity_cards(st, opps)

    # ── اعتماد الأسعار: يكتب سطراً إلحاقيّاً في price_history فقط (لا إرسال) ──
    st.divider()
    st.subheader("✍️ اعتماد الأسعار")
    st.caption(
        "«موافقة» تعتمد السعر المقترَح · «تعديل» تعتمد قيمتك — كلاهما يُسجَّل حدثاً "
        "مستقلاً في سجلّ القرارات (price_history). لا إرسال إلى أي مكان."
    )
    from utils.db_manager import append_price_decision  # استيراد كسول (كتابة القاعدة)

    last = _last_decisions()
    for idx, o in enumerate(opps):
        median = o.price_median or 0
        suggested = o.suggested_price
        is_stale = (o.stale_reason == "stale")
        c_name, c_appr, c_val, c_edit = st.columns([5, 1.9, 1.8, 1.3])
        ellipsis = "…" if len(o.norm_name) > 52 else ""
        c_name.markdown(f"**{o.norm_name[:52]}{ellipsis}**")
        prev = last.get(o.norm_name)
        if prev:
            c_name.caption(f"آخر قرار: {prev[0]} · {prev[1]:g} ر.س · {prev[2]}")

        # ── بيانات قديمة: عطّل الاعتماد والتعديل — لا يُسجَّل قرار على بيانات قديمة ──
        if is_stale:
            c_name.caption("🕒 بيانات قديمة — حدّث الكشط قبل اتخاذ قرار")
            c_appr.button("✅ موافقة", key=f"opp_appr_{idx}", disabled=True,
                          use_container_width=True)
            c_val.caption("—")
            c_edit.button("✏️ تعديل", key=f"opp_editbtn_{idx}", disabled=True,
                          use_container_width=True)
            continue

        # ✅ موافقة بالسعر المقترَح (إن وُجد)
        if suggested is not None:
            if c_appr.button(f"✅ موافقة ({suggested:g})", key=f"opp_appr_{idx}",
                             use_container_width=True):
                append_price_decision(
                    o.norm_name, our_price=suggested, price=median,
                    decision="approved",
                )
                st.success(
                    f"✅ سُجِّلت موافقة على «{o.norm_name[:36]}» بسعر {suggested:g} ر.س."
                )
        else:
            c_appr.caption("لا سعر مقترَح")

        # ✏️ تعديل: حقل رقمي + زرّ
        default_val = float(suggested if suggested is not None else median)
        new_val = c_val.number_input(
            "تعديل السعر", min_value=0.0, value=default_val, step=1.0,
            key=f"opp_editval_{idx}", label_visibility="collapsed",
        )
        if c_edit.button("✏️ تعديل", key=f"opp_editbtn_{idx}", use_container_width=True):
            append_price_decision(
                o.norm_name, our_price=round(new_val), price=median,
                decision="edited",
            )
            st.success(
                f"✏️ سُجِّل تعديل «{o.norm_name[:36]}» إلى {round(new_val):g} ر.س."
            )
