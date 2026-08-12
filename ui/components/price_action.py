"""ui/components/price_action.py — إجراء سعري فردي على البطاقة (استعادة من القديم).

يعرض «🎯 السعر المستهدف» + «🚀 تحديث السعر (Make)» + «🚫 تجاهل» لكل منتج في الأقسام
السعرية، يرسل المنتج الواحد إلى Make عبر ``ExportService`` (بلا تعديل عليها)
ويعلّمه معالَجاً (يَنجو عبر اللقطة القرصية). ``suggested_price`` خالصة قابلة للاختبار.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

from services.decision_journal import record_decision
from services.export_service import ExportService
from ui.components.product_card import CardView, build_card
from ui.state_manager import AppState
from utils.data_helpers import first_image_url_string

# خريطة المفتاح → نوع القسم لحمولة Make (#PRESERVED_LOGIC export_service._section_price).
_SECTION_TYPE: dict[str, str] = {
    "price_raise": "raise", "price_lower": "lower", "approved": "approved",
}


def suggested_price(card: CardView, section_value: str) -> float:
    """السعر المستهدف المقترح: منافس−1 للرفع/الخفض، وسعرنا للموافق. خالص.

    #PRESERVED_LOGIC: app.py:6148 ``max(round(comp-1), 1)`` للأقسام السعرية.
    """
    if section_value in ("price_raise", "price_lower") and card.comp_price > 0:
        return float(max(round(card.comp_price - 1), 1))
    if card.our_price > 0:
        return float(round(card.our_price))
    return float(round(card.comp_price)) if card.comp_price > 0 else 0.0


def _row_visual_data(row: Any, card: CardView, section_value: str) -> dict:
    """يستخرج بيانات بصرية من الصف لحفظها في سجلّ المعالَجة (للبطاقة الاحترافية)."""
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    # أولوية الصورة: 1) CardView.our_image (مُنظّف مسبقاً) 2) عمود الصورة الخام (يُنظَّف)
    raw_img = str(getter("صورة_منتجنا", "") or getter("صورة المنتج", "") or "")
    image = card.our_image if card.our_image else first_image_url_string(raw_img)
    return {
        "image": image,
        "price": card.our_price,
        "brand": str(getter("الماركة", "") or ""),
        "section": section_value,
        "comp_name": card.comp_name,
        "comp_price": card.comp_price,
        "our_link": card.our_link,
        "comp_link": card.comp_link,
    }


def _send_update(
    st: Any, row: Any, section_value: str, target: float,
    state: AppState, card: CardView, *, band_override_reason: str = "",
) -> None:
    """يرسل تحديث سعر منتج واحد إلى Make (WEBHOOK_UPDATE_PRICES) ويعلّمه معالَجاً."""
    url = os.environ.get("WEBHOOK_UPDATE_PRICES", "")
    if not url:
        st.error("⚠️ رابط Webhook غير مهيّأ (.env)")
        return
    service = ExportService()
    products = service.make_payload(
        pd.DataFrame([row]), _SECTION_TYPE.get(section_value, "update"),
    )
    if not products:
        st.warning("بيانات ناقصة (لا اسم/معرّف) — تعذّر الإرسال")
        return
    # ── تحقق من وجود Salla ID (NO) — شرط إلزامي ──
    if not products[0].get("NO"):
        st.error("⚠️ رقم المنتج (NO / Salla ID) مفقود — تعذّر الإرسال. تأكد من وجود عمود No أو رقم_المنتج في الكتالوج.")
        return
    products[0]["price"] = float(target)  # احترام السعر المستهدف الذي اختاره المستخدم
    if band_override_reason.strip():
        products[0]["_band_override_reason"] = band_override_reason.strip()
    try:
        with st.spinner("جاري إرسال تحديث السعر…"):
            result = service.post_to_make(url, products)
    except Exception as exc:
        st.error(f"❌ خطأ في الاتصال بـ Make: {exc}")
        return
    if result.get("confirmed"):
        st.session_state.pop(_band_override_state_key(card), None)
        # ── بروتوكول الإخلاء الكامل ──
        state.mark_price_processed(card.our_sku)   # تعليم كـ«معالَج» (يُستبعد من visible_dataframe)
        state.hide(card.our_name)                  # إخفاء بالاسم أيضاً (طبقة أمان ثانية)
        state.log_action(
            key=card.our_sku, name=card.our_name, action="تحديث السعر",
            detail=f"السعر المستهدف {target:,.0f} ر.س ← Make", kind="price",
            row_data=_row_visual_data(row, card, section_value),
        )
        # تسجيل صامت: المقترَح مقابل المختار (دفتر القرارات — لا يؤثّر على الإرسال)
        record_decision("send_price", card.our_name, section_value,
                        suggested=suggested_price(card, section_value),
                        chosen=float(target))
        state.persist_results()  # تثبيت «تمت المعالجة» على القرص فوراً
        st.success(f"✅ أُرسل تحديث السعر: {target:,.0f} ر.س")
        st.rerun()  # ⚡ إخلاء فوري — البطاقة تختفي من القسم الحالي
    else:
        if result.get("state") == "accepted":
            st.warning(
                "📨 استلم Make الطلب، لكن تحديث سلة لم يتأكد بعد؛ أبقينا المنتج في قائمة المراجعة."
            )
            return
        if result.get("state") == "held_outside_band":
            reason = (result.get("held_outside_band") or [{}])[0].get("blocked_reason", "")
            st.session_state[_band_override_state_key(card)] = {
                "target": float(target), "guard_reason": reason,
            }
            st.warning(f"🛑 حُجز التحديث خارج نطاق السعر. {reason}")
            return
        # رسالة الخطأ الحقيقية من Make (response.text) — لا رقم الحالة فقط.
        error_body = result.get('body') or result.get('error') or ''
        status = result.get('status_code', '?')
        st.error(f"❌ فشل الإرسال (HTTP {status}): {error_body}")



def _send_quantity_update(
    st: Any, row: Any, quantity: int, card: CardView,
) -> None:
    """يرسل تحديث كمية منتج واحد إلى Make (``WEBHOOK_UPDATE_QUANTITY``) — لا يمسّ السعر.

    تصفير الكمية (0) = تعليم المنتج «نفذ لدينا» في سلة. مستقل عن مسار التسعير:
    لا يغيّر حالة المعالجة ولا يُخفي البطاقة (الإرسال فقط + تغذية راجعة واضحة).
    """
    url = os.environ.get("WEBHOOK_UPDATE_QUANTITY", "")
    if not url:
        st.error("⚠️ رابط Webhook الكمية غير مهيّأ (WEBHOOK_UPDATE_QUANTITY)")
        return
    service = ExportService()
    items = service.quantity_payload(pd.DataFrame([row]), quantity)
    if not items:
        st.warning("بيانات ناقصة (لا اسم) — تعذّر تحديث الكمية")
        return
    # ── شرط إلزامي: Salla ID (NO) موجود (نفس حارس تحديث السعر) ──
    if not items[0].get("NO"):
        st.error("⚠️ رقم المنتج (NO / Salla ID) مفقود — تعذّر تحديث الكمية.")
        return
    try:
        with st.spinner("جاري تحديث المخزون…"):
            result = service.post_to_make(url, items)
    except Exception as exc:
        st.error(f"❌ خطأ في الاتصال بـ Make: {exc}")
        return
    if result.get("confirmed"):
        label = "نفذ (الكمية = 0)" if quantity == 0 else f"الكمية = {quantity}"
        st.success(f"✅ أُرسل تحديث المخزون «{card.our_name[:30]}»: {label}")
    elif result.get("state") == "accepted":
        st.warning(
            "📨 استلم Make الطلب، لكن تحديث سلة لم يتأكد بعد؛ لم نعرضه كنجاح نهائي."
        )
    else:
        # رسالة الخطأ الحقيقية من Make (response.text) — لا رقم الحالة فقط.
        error_body = result.get("body") or result.get("error") or ""
        status = result.get("status_code", "?")
        st.error(f"❌ فشل الإرسال (HTTP {status}): {error_body}")


def _ignore_product(
    st: Any, row: Any, state: AppState, card: CardView, section_value: str,
) -> None:
    """يتجاهل المنتج: يخفيه من القسم ويسجّله في المعالَجة."""
    state.hide(card.our_name)
    state.log_action(
        key=card.our_sku or card.our_name, name=card.our_name,
        action="تجاهل", detail="أُزيل يدوياً من قائمة المراجعة",
        kind="hidden",
        row_data=_row_visual_data(row, card, section_value),
    )
    # تسجيل صامت: تجاهل (مع المقترَح المتاح لحظتها)
    record_decision("ignore", card.our_name, section_value,
                    suggested=suggested_price(card, section_value))
    state.persist_results()
    st.toast(f"🚫 تم تجاهل «{card.our_name[:40]}»", icon="🚫")
    st.rerun()  # ⚡ إخلاء فوري — البطاقة تختفي من القسم الحالي


def _band_override_state_key(card: CardView) -> str:
    return f"_band_override_{card.our_sku or card.our_name}"


def _render_band_override(
    st: Any, row: Any, state: AppState, card: CardView,
    section_value: str, key: str,
) -> None:
    """واجهة تجاوز لمرة واحدة: لا إرسال خارج النطاق بلا سبب صريح."""
    pending = st.session_state.get(_band_override_state_key(card))
    if not isinstance(pending, dict):
        return

    with st.expander("🛑 تجاوز مسجّل لسياج السعر", expanded=True):
        st.caption(str(pending.get("guard_reason") or "خارج نطاق ±20% من وسيط السوق"))
        reason = st.text_area(
            "سبب التجاوز (إلزامي)", key=f"band_reason_{key}",
            placeholder="مثال: عرض رسمي موثق من المنافس أو قرار تسعير موسمي",
        )
        confirm_col, cancel_col = st.columns(2)
        if cancel_col.button("إلغاء", key=f"band_cancel_{key}", use_container_width=True):
            st.session_state.pop(_band_override_state_key(card), None)
            st.rerun()
        if confirm_col.button("تجاوز وإرسال", key=f"band_confirm_{key}", type="primary",
                              use_container_width=True):
            if not reason.strip():
                st.error("اكتب سبب التجاوز قبل الإرسال.")
                return
            _send_update(
                st, row, section_value, float(pending.get("target") or 0), state, card,
                band_override_reason=reason,
            )


def render_price_action(
    row: Any, state: AppState, section_value: str, *, key_prefix: str,
) -> None:
    """يعرض «🎯 السعر المستهدف» + «🚀 تحديث السعر» + «🚫 تجاهل» لمنتج واحد."""
    import streamlit as st

    card = build_card(row)
    if card.comp_price <= 0:  # لا منافس للتسعير ضدّه ⇒ لا إجراء
        return
    key = f"{key_prefix}_{card.our_sku.strip() or id(row)}"
    processed = state.is_price_processed(card.our_sku)
    ignored = state.is_hidden(card.our_name)

    # ── إشارة حالة واضحة ──
    if processed:
        st.success("✅ تم تحديث السعر — المنتج في «تمت المعالجة»", icon="✅")
        return
    if ignored:
        st.warning("🚫 تم تجاهل هذا المنتج", icon="🚫")
        return

    # ── تخطيط محسّن: [سعر مستهدف] [تجاهل صغير] [Make بارز] ──
    col_price, col_ignore, col_send = st.columns([3.5, 1, 3.5])
    target = col_price.number_input(
        "🎯 السعر المستهدف (ر.س)", value=suggested_price(card, section_value),
        min_value=0.0, step=1.0, key=f"tp_{key}",
        help="السعر الجديد المقترح للمنتج",
    )
    # زر تجاهل (رمادي، صغير)
    if col_ignore.button(
        "🚫 تجاهل", key=f"ign_{key}",
        help="تجاهل هذا المنتج (يُخفى من القسم)",
        use_container_width=True,
    ):
        _ignore_product(st, row, state, card, section_value)
    # زر Make (بارز، كامل العرض)
    label = "🚀 تحديث السعر (Make)"
    if col_send.button(label, key=f"upd_{key}", type="primary",
                       use_container_width=True):
        _send_update(st, row, section_value, float(target), state, card)

    _render_band_override(st, row, state, card, section_value, key)

    # ── إدارة المخزون: تحديث الكمية / تصفير (0 ⇒ «نفذ لدينا») — اختياري، لا يمسّ السعر ──
    with st.expander("📦 إدارة المخزون", expanded=False):
        qty = st.number_input(
            "الكمية", min_value=0, step=1, value=0, key=f"qty_{key}",
            help="0 = نفذ (يُخفى من المتجر). تحديث مستقل عن السعر.",
        )
        confirmed = st.checkbox(
            "أؤكّد تحديث المخزون", key=f"qtyok_{key}",
            help="حماية من التصفير العرضي",
        )
        if st.button(
            "📦 تحديث المخزون (Make)", key=f"qtybtn_{key}",
            disabled=not confirmed, use_container_width=True,
        ):
            _send_quantity_update(st, row, int(qty), card)
