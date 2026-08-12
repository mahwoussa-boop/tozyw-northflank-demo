"""ui/pages/product_factory.py — ✨ مصنع المنتجات (إنشاء منتجات جديدة).

تعرض نموذج إنشاء منتج جديد يدوياً مع التحقق من التكرار
وإعداد المنتج للتصدير إلى Make/Salla.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ui.state_manager import AppState


def _check_duplicate(name: str, catalog: pd.DataFrame | None) -> bool:
    """يفحص إذا كان المنتج موجوداً في الكتالوج."""
    if catalog is None or catalog.empty or "المنتج" not in catalog.columns:
        return False
    from services.matching_service import normalize_name
    norm_new = normalize_name(name)
    for existing in catalog["المنتج"].dropna().astype(str):
        if normalize_name(existing) == norm_new:
            return True
    return False


def _build_product(data: dict[str, Any]) -> dict[str, Any]:
    """يبني منتجاً من البيانات المُدخلة."""
    from services.export_service import _build_new_product
    # تحويل البيانات لصف يمكن معالجته
    row = pd.Series({
        "المنتج": data.get("name", ""),
        "السعر": data.get("price", 0),
        "سعر_المنافس": data.get("comp_price", 0),
        "الماركة": data.get("brand", ""),
        "sku": data.get("sku", ""),
        "الوزن": data.get("weight", 1),
        "سعر التكلفة": data.get("cost_price", 0),
        "السعر المخفض": data.get("sale_price", 0),
        "الوصف": data.get("description", ""),
        "صورة المنتج": data.get("image", ""),
        "اسم التصنيف": data.get("category", ""),
    })
    return _build_new_product(row) or {}


def render(state: AppState, *, container: Optional[Any] = None) -> None:
    """يعرض صفحة مصنع المنتجات."""
    import streamlit as st

    from ui.components.page_header import render_page_header

    render_page_header("مصنع المنتجات", section="factory")

    # ── شرح الصفحة ──
    st.markdown(
        '<div style="background:linear-gradient(135deg,#1E293B,#0F172A);'
        'border:1px solid #334155;border-radius:14px;padding:16px 20px;'
        'font-family:Tajawal,sans-serif;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px;direction:rtl">'
        '<span style="font-size:2rem">✨</span>'
        '<div>'
        '<div style="font-weight:800;color:#E2E8F0;font-size:1.05rem">'
        'إنشاء منتج جديد</div>'
        '<div style="font-size:.82rem;color:#94A3B8;line-height:1.5;margin-top:4px">'
        'أضف منتجاً جديداً يدوياً للكتالوج. '
        'المنتج يُفحص تلقائياً للتأكد من عدم التكرار، '
        'و يُجهّز للتصدير إلى Make/Salla.'
        '</div></div></div></div>',
        unsafe_allow_html=True,
    )

    # ── نموذج إنشاء المنتج ──
    st.subheader("📝 بيانات المنتج")

    with st.form("product_factory_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(
                "اسم المنتج *", placeholder="مثال: كريد أفينتوس 100مل",
                help="الاسم الكامل للمنتج مع الحجم",
            )
            price = st.number_input(
                "سعر البيع (ريال) *", min_value=0.0, step=1.0,
                help="سعر البيع في متجرك",
            )
            brand = st.text_input(
                "الماركة", placeholder="مثال: Creed",
                help="ماركة المنتج",
            )
            sku = st.text_input(
                "SKU / رمز المنتج", placeholder="مثال: CREED-AVT-100",
                help="رمز المنتج الفريد",
            )
            category = st.text_input(
                "التصنيف", placeholder="مثال: عطور رجالية",
                help="تصنيف المنتج في المتجر",
            )

        with c2:
            comp_price = st.number_input(
                "سعر المنافس (ريال)", min_value=0.0, step=1.0,
                help="سعر المنافس (للتسعير التنافسي)",
            )
            cost_price = st.number_input(
                "سعر التكلفة (ريال)", min_value=0.0, step=1.0,
                help="سعر الشراء/التكلفة",
            )
            sale_price = st.number_input(
                "السعر المخفض (ريال)", min_value=0.0, step=1.0,
                help="السعر بعد التخفيض (0 = لا تخفيض)",
            )
            weight = st.number_input(
                "الوزن (كغ)", min_value=0.0, value=1.0, step=0.1,
                help="وزن المنتج للشحن",
            )
            image_url = st.text_input(
                "رابط الصورة", placeholder="https://example.com/image.jpg",
                help="رابط صورة المنتج",
            )

        description = st.text_area(
            "الوصف", placeholder="وصف المنتج التفصيلي...",
            help="وصف كامل للمنتج",
        )

        st.markdown("---")
        submitted = st.form_submit_button(
            "✨ إنشاء المنتج", type="primary", use_container_width=True,
        )

    if submitted:
        if not name or price <= 0:
            st.error("❌ اسم المنتج والسعر مطلوبان")
            return

        # فحص التكرار
        catalog = state.our_catalog
        if _check_duplicate(name, catalog):
            st.warning(
                f"⚠️ المنتج '{name}' موجود في الكتالوج بالفعل — "
                "تحقق من عدم التكرار"
            )
            return

        # بناء المنتج
        product_data = {
            "name": name, "price": price, "comp_price": comp_price,
            "brand": brand, "sku": sku, "weight": weight,
            "cost_price": cost_price, "sale_price": sale_price,
            "description": description, "image": image_url,
            "category": category,
        }
        product = _build_product(product_data)

        if not product:
            st.error("❌ تعذّر بناء المنتج — تحقق من البيانات")
            return

        st.success("✅ تم إنشاء المنتج بنجاح!")
        st.json(product)

        # عرض زر التصدير
        st.markdown("---")
        st.subheader("📤 تصدير المنتج")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("📤 إرسال إلى Make (منتج جديد)", use_container_width=True, key="send_new"):
                if container is not None and container.export:
                    try:
                        from conf.settings import Settings
                        settings = Settings.load()
                        url = settings.webhook_new_products
                        if not url:
                            st.error("❌ WEBHOOK_NEW_PRODUCTS غير مهيّأ في `.env`")
                        else:
                            result = container.export.post_to_make(url, [product], envelope="data")
                            if result.get("success"):
                                st.success("✅ تم الإرسال بنجاح!")
                            else:
                                st.error(f"❌ فشل الإرسال: {result.get('error', '—')}")
                    except Exception as exc:
                        st.error(f"❌ خطأ: {exc}")
                else:
                    st.error("❌ خدمة التصدير غير متوفرة")

        with c2:
            csv_data = pd.DataFrame([product]).to_csv(index=False)
            st.download_button(
                "📥 تحميل CSV", data=csv_data, file_name=f"{name}.csv",
                mime="text/csv", use_container_width=True, key="download_csv",
            )

    # ── قائمة المنتجات المُنشأة حديثاً ──
    st.markdown("---")
    st.subheader("📦 المنتجات المُنشأة حديثاً")
    st.info(
        "💡 المنتجات المُنشأة هنا لا تُضاف تلقائياً للكتالوج. "
        "أضفها عبر ملف Excel/CSV ثم ارفعها في لوحة التحكم."
    )
