"""ui/components/card_actions.py — إجراءات فردية على بطاقة المفقودات (استعادة من القديم).

- ``render_quick_send``: «⚡ إرسال سريع» يرسل منتجاً مفقوداً إلى Make
  (WEBHOOK_NEW_PRODUCTS) بسعر منافس−1، بلا إثراء، ويعلّمه معالَجاً.
- ``render_ai_verify``: «🤖 تحقّق AI» يسأل الذكاء الاصطناعي إن كنّا نملك المنتج فعلاً.
يستخدمان الخدمات المحقونة كما هي (بلا أي تعديل على services/).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import pandas as pd

from conf.constants import (
    COL_BRAND,
    COL_COMP_LINK,
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_OUR_ID,
    COL_OUR_NAME,
    COL_OUR_PRICE,
    COL_SIZE,
    COL_TYPE,
)
from utils.data_helpers import first_image_url_string

_VERIFY_SYSTEM = (
    "أنت خبير عطور تحدّد إن كان منتج المنافس مطابقاً لمنتج نملكه (بصيغة/اسم آخر) "
    "أم منتجاً جديداً مفقوداً من متجرنا. كن دقيقاً وموجزاً."
)


def _g(row: Any, key: str, default: Any = "") -> Any:
    """قراءة آمنة من صف/قاموس (تُرجع الافتراضي عند None/NaN)."""
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    value = getter(key, default)
    return default if value is None or str(value) == "nan" else value


def _price(row: Any) -> float:
    """سعر المنافس بأمان (0.0 عند التعذّر)."""
    try:
        return float(_g(row, COL_COMP_PRICE, 0))
    except (TypeError, ValueError):
        return 0.0


def _missing_row_data(row: Any) -> dict:
    """بيانات بصرية لمنتج مفقود (للبطاقة الاحترافية في المعالَجة)."""
    raw_img = str(_g(row, "صورة_المنافس", "") or _g(row, "رابط_الصورة", "") or _g(row, "الصورة", ""))
    return {
        "image": first_image_url_string(raw_img),
        "price": _price(row),
        "brand": str(_g(row, COL_BRAND, "")),
        "section": "missing",
        "comp_name": str(_g(row, COL_COMP_NAME, "")),
        "comp_price": _price(row),
        "comp_link": str(_g(row, COL_COMP_LINK, "")),
    }


def _append_confirmed_catalog_row(state: Any, catalog_row: Any) -> bool:
    """يحدّث لقطة كتالوج الجلسة بعد مزامنة DB الناجحة، بلا تكرار."""
    if not isinstance(catalog_row, dict):
        return False
    product_id = str(catalog_row.get("product_id") or "").strip()
    product_name = str(catalog_row.get("product_name") or "").strip()
    if not product_id or not product_name:
        return False
    try:
        price = float(catalog_row.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    frame = getattr(state, "our_catalog", None)
    record = {
        COL_OUR_ID: product_id, COL_OUR_NAME: product_name, COL_OUR_PRICE: price,
    }
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        state.our_catalog = pd.DataFrame([record])
        return True
    updated = frame.copy()
    if COL_OUR_ID in updated.columns:
        matched = updated[COL_OUR_ID].astype(str).str.strip() == product_id
        if matched.any():
            target = matched.idxmax()
            for column, value in record.items():
                if column in updated.columns:
                    updated.at[target, column] = value
            state.our_catalog = updated
            return True
    row = {column: None for column in updated.columns}
    for column, value in record.items():
        if column in row:
            row[column] = value
    state.our_catalog = pd.concat([updated, pd.DataFrame([row])], ignore_index=True)
    return True


def render_quick_send(
    row: Any, state: Any, export_service: Optional[Any], *, key: str,
) -> None:
    """«⚡ إرسال سريع» — يرسل منتجاً مفقوداً إلى Make بسعر منافس−1 ويعلّمه معالَجاً.

    القاعدة الذهبية: يتطلب إثراء المنتج (كشط + تنسيق) قبل الإرسال.
    يتحقق من وجود الماركة في سلة — إذا غير موجودة يُنتج ملف استيراد ماركة.
    """
    import streamlit as st

    if export_service is None:
        return
    link = str(_g(row, COL_COMP_LINK))
    processed = link in state.processed_missing_urls
    # ── القاعدة الذهبية: هل تم إثراء المنتج؟ ──
    from services.enrichment_service import is_enriched
    enriched = is_enriched(row)

    # ── فحص الماركة ──
    brand_name = str(_g(row, COL_BRAND, "")).strip()
    brand_ok = True
    try:
        from services.brand_manager import is_brand_known, build_brand_row, generate_brands_excel, add_to_known_brands
        if brand_name and brand_name not in ("غير متوفر", "غير محدد", "nan", "None"):
            brand_ok = is_brand_known(brand_name)
            if not brand_ok and not processed:
                # تحقق إذا أكّد المستخدم تحميل هذه الماركة مسبقاً
                if st.session_state.get(f"brand_ok_{key}"):
                    brand_ok = True
                else:
                    st.warning(f"⚠️ ماركة **{brand_name}** غير موجودة في سلة")
                    c1, c2 = st.columns(2)
                    with c1:
                        try:
                            row_data = build_brand_row(brand_name)
                            excel = generate_brands_excel([row_data])
                            st.download_button(
                                f"📥 تنزيل ملف ماركة {brand_name}",
                                data=excel,
                                file_name=f"ماركة_{brand_name}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key=f"brand_dl_{key}",
                                use_container_width=True,
                            )
                        except Exception as exc:
                            st.warning(f"تعذّر تجهيز ملف ماركة {brand_name}: {exc}")
                    with c2:
                        if st.button("✅ تم التحميل", key=f"brand_conf_{key}",
                                     type="primary", use_container_width=True):
                            add_to_known_brands([brand_name])
                            st.session_state[f"brand_ok_{key}"] = True
                            st.rerun()
    except ImportError:
        brand_ok = True

    label = "✅ أُرسل" if processed else ("⚡ إرسال سريع" if (enriched and brand_ok) else "⚠️ يتطلب الكشط أولاً" if not enriched else "⏳ أضف الماركة أولاً")
    can_send = enriched and brand_ok
    if not st.button(label, key=f"qs_{key}", disabled=processed or not can_send,
                     help="" if can_send else "يجب إضافة الماركة أو تشغيل الكشط أولاً",
                     use_container_width=True):
        return
    webhook = os.environ.get("WEBHOOK_NEW_PRODUCTS", "")
    if not webhook:
        st.error("⚠️ رابط Webhook غير مهيّأ (.env)")
        return
    # المنتج الجديد/المفقود يُرسَل بمظروف «data» ومفاتيح سلة العربية (مخطط CreateProduct)،
    # لا «products»/مفاتيح إنجليزية (ذاك لتحديث الأسعار فقط). السعر يُحسَب داخل الباني (منافس−1).
    items = export_service.new_products_payload(pd.DataFrame([row]))
    if not items:
        st.warning("بيانات ناقصة (لا اسم/سعر صالح) — تعذّر الإرسال")
        return
    sent_price = float(items[0].get("سعر المنتج", 0))
    with st.spinner("جاري الإرسال لـ Make…"):
        result = export_service.post_to_make(webhook, items, envelope="data")
    if result.get("confirmed"):
        if result.get("catalog_synced"):
            _append_confirmed_catalog_row(state, result.get("catalog_row"))
        state.mark_missing_processed(link)
        state.log_action(
            key=link, name=str(_g(row, COL_COMP_NAME)), action="إرسال سريع (مفقود)",
            detail=f"بسعر {sent_price:,.0f} ر.س ← Make", kind="missing",
            row_data=_missing_row_data(row),
        )
        state.persist_results()
        st.success(f"✅ أُرسل بسعر {sent_price:,.0f} ر.س")
        st.rerun()  # ⚡ إخلاء فوري — البطاقة تختفي من قسم المفقودات
    elif result.get("state") == "accepted":
        # 202/2xx فارغ = استلم Make الطلب فقط؛ لا نخفي بطاقة المنتج ولا
        # نعلّمه كمُعالَج قبل عودة product_id مؤكّد من سلة.
        st.warning(
            "📨 استلم Make الطلب، لكن إنشاء المنتج لم يتأكد في سلة بعد. "
            "أبقينا البطاقة ظاهرة لحمايتها من نجاح كاذب."
        )
    else:
        # رسالة الخطأ الحقيقية من Make (response.text) — لا رقم الحالة فقط.
        st.error(
            "❌ فشل الإرسال: "
            f"{result.get('body') or result.get('error') or result.get('status_code')}"
        )



def render_ai_verify(row: Any, ai_service: Optional[Any], *, key: str) -> None:
    """«🤖 تحقّق AI» — يسأل الذكاء الاصطناعي إن كنّا نملك المنتج فعلاً (مطابقة مشكوكة)."""
    import streamlit as st

    if not st.button("🤖 تحقّق AI", key=f"av_{key}", disabled=ai_service is None,
                     use_container_width=True):
        return
    name = str(_g(row, COL_COMP_NAME))
    prompt = (
        f"منتج المنافس: «{name}»\n"
        f"الماركة: {_g(row, COL_BRAND)} · الحجم: {_g(row, COL_SIZE)} · "
        f"النوع: {_g(row, COL_TYPE)}\n"
        f"السعر: {_price(row):,.0f} ر.س\n\n"
        "هل من المرجّح أننا نملك هذا المنتج (باسم/صيغة أخرى) أم أنه مفقود فعلاً؟ "
        "أجب بسطر واحد: [نملكه / مفقود / غير مؤكد] ثم سبب موجز."
    )
    with st.spinner("يحلّل الذكاء الاصطناعي…"):
        result = ai_service.call(prompt, _VERIFY_SYSTEM)
    (st.info if result.success else st.warning)(
        result.response or "تعذّر التحقّق عبر AI")
