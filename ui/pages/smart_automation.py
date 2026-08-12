"""ui/pages/smart_automation.py — 🔄 الأتمتة الذكية.

تعرض قواعد الأتمتة وتسمح بضبطها:
- قواعد التسعير التلقائي
- قواعد التصدير التلقائي
- قواعد التنبيه
- حالة الطيار الآلي (Autopilot)
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ui.state_manager import AppState


_RULES = [
    {
        "name": "رفع سعر تلقائي",
        "description": "إذا كان سعرنا أقل بـ 10% من المنافس → ارفع السعر تلقائياً",
        "enabled": False,
        "icon": "🟢",
    },
    {
        "name": "خفض سعر تلقائي",
        "description": "إذا كان سعرنا أعلى بـ 15% من المنافس → خفض السعر تلقائياً",
        "enabled": False,
        "icon": "🔴",
    },
    {
        "name": "تصدير تلقائي للمفقودات",
        "description": "تصدير المنتجات المفقودة المؤكدة إلى Make كل 24 ساعة",
        "enabled": False,
        "icon": "📤",
    },
    {
        "name": "تنبيه الأسعار الشاذة",
        "description": "إرسال تنبيه عند اكتشاف سعر شاذ (مرتفع جداً أو منخفض جداً)",
        "enabled": True,
        "icon": "⚠️",
    },
    {
        "name": "تنبيه المنافس الجديد",
        "description": "تنبيه عند ظهور منتج جديد عند المنافسين",
        "enabled": True,
        "icon": "🆕",
    },
    {
        "name": "تنبيه النفاد",
        "description": "تنبيه عند نفاد منتج ساخن لدينا وموجود عند المنافسين",
        "enabled": True,
        "icon": "🔴",
    },
]


def _render_rule_card(rule: dict, index: int) -> None:
    """يعرض بطاقة قاعدة أتمتة."""
    import streamlit as st

    enabled = rule["enabled"]
    color = "#10B981" if enabled else "#6B7280"
    status = "مفعّل" if enabled else "معطّل"

    st.markdown(
        f'<div style="background:linear-gradient(180deg,rgba(22,22,26,.95),rgba(15,15,18,.95));'
        f'border:1px solid #1F2937;border-right:3px solid {color};border-radius:14px;'
        f'padding:16px 20px;margin:8px 0;direction:rtl;font-family:Tajawal,sans-serif">'
        f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
        f'<span style="font-size:1.8rem">{rule["icon"]}</span>'
        f'<div style="flex:1;min-width:0">'
        f'<div style="font-size:1rem;font-weight:700;color:#E2E8F0">{rule["name"]}</div>'
        f'<div style="font-size:.82rem;color:#94A3B8;margin-top:2px">{rule["description"]}</div>'
        f'</div>'
        f'<span style="font-size:.75rem;font-weight:700;color:{color};'
        f'background:{color}1A;border:1px solid {color}44;'
        f'border-radius:8px;padding:3px 10px;white-space:nowrap">{status}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # زر التفعيل/التعطيل
    key = f"toggle_rule_{index}"
    if st.button(
        "🔴 تعطيل" if enabled else "🟢 تفعيل",
        key=key,
        use_container_width=True,
    ):
        rule["enabled"] = not enabled
        st.rerun()


def render(state: AppState, *, container: Optional[Any] = None) -> None:
    """يعرض صفحة الأتمتة الذكية."""
    import streamlit as st

    from ui.components.page_header import render_page_header

    render_page_header("الأتمتة الذكية", section="automation")

    # ── شرح الصفحة ──
    st.markdown(
        '<div style="background:linear-gradient(135deg,#1E293B,#0F172A);'
        'border:1px solid #334155;border-radius:14px;padding:16px 20px;'
        'font-family:Tajawal,sans-serif;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px;direction:rtl">'
        '<span style="font-size:2rem">🤖</span>'
        '<div>'
        '<div style="font-weight:800;color:#E2E8F0;font-size:1.05rem">'
        'الطيار الآلي</div>'
        '<div style="font-size:.82rem;color:#94A3B8;line-height:1.5;margin-top:4px">'
        'قواعد أتمتة ذكية تُنفّذ قرارات تلقائية بناءً على شروط محددة. '
        '<b style="color:#F59E0B">ملاحظة:</b> '
        'التنبيهات مفعّلة تلقائياً؛ الإجراءات التلقائية (تغيير السعر/التصدير) تتطلب تفعيل يدوي.'
        '</div></div></div></div>',
        unsafe_allow_html=True,
    )

    # ── حالة الطيار الآلي ──
    if container is not None:
        try:
            autopilot = container.autopilot
            status = autopilot.get_status()
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("حالة الطيار", "مفعّل" if status.get("running") else "متوقف")
            with col2:
                st.metric("القرارات التلقائية", f"{status.get('decisions', 0):,}")
            with col3:
                st.metric("آخر تشغيل", status.get("last_run", "—"))
        except Exception:
            st.info("ℹ️ الطيار الآلي غير مهيّأ — أضف مفاتيح API لتفعيله")

    st.markdown("---")

    # ── قائمة القواعد ──
    st.subheader("📋 قواعد الأتمتة")

    for i, rule in enumerate(_RULES):
        _render_rule_card(rule, i)

    st.markdown("---")

    # ── إحصائيات الأتمتة ──
    st.subheader("📊 إحصائيات الأتمتة")

    if container is not None:
        try:
            # إشعارات
            unread = container.notifier.unread_count()
            total = len(container.notifier.get_unread(limit=1000))
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("إشعارات غير مقروءة", f"{unread:,}")
            with c2:
                st.metric("إجمالي الإشعارات", f"{total:,}")
            with c3:
                if container.ai.any_configured:
                    st.metric("AI", "✅ مفعّل")
                else:
                    st.metric("AI", "⚠️ معطّل")
        except Exception:
            st.info("الإحصائيات غير متوفرة حالياً")
    else:
        st.info("الإحصائيات تتطلب تهيئة الخدمات")

    # ── تنبيه ──
    st.markdown("---")
    st.info(
        "💡 **قريباً:** سيتم إضافة قواعد مخصصة و جدولة زمنية للأتمتة. "
        "يمكنك الآن مراجعة الإشعارات في الشريط الجانبي."
    )
