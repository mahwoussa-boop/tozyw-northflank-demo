"""ui/pages/make_automation.py — ⚡ أتمتة Make (Webhooks).

تعرض حالة webhooks وتسمح بإرسال اختبار ومراجعة سجل الإرسال.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ui.state_manager import AppState


def _read_env() -> dict[str, str]:
    """يقرأ متغيرات البيئة من .env."""
    import os
    from pathlib import Path
    env: dict[str, str] = {}
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _test_webhook(url: str, payload: dict) -> dict[str, Any]:
    """يرسل اختبار لـ webhook."""
    import requests
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Content-Type": "application/json"}, timeout=15,
        )
        return {
            "success": resp.status_code in (200, 201, 202, 204),
            "status_code": resp.status_code,
            "body": (resp.text or "")[:500],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _send_test_update(env: dict[str, str]) -> dict[str, Any]:
    """يرسل منتج اختبار لتحديث الأسعار."""
    url = env.get("WEBHOOK_UPDATE_PRICES", "")
    if not url:
        return {"success": False, "error": "WEBHOOK_UPDATE_PRICES غير مهيّأ"}
    payload = {
        "products": [
            {
                "NO": "TEST-001",
                "product_id": "TEST-001",
                "name": "منتج اختبار — يمكنك حذفه",
                "price": 999,
                "section": "update",
                "competitor": "اختبار",
                "price_diff": 0,
            }
        ]
    }
    return _test_webhook(url, payload)


def _send_test_new(env: dict[str, str]) -> dict[str, Any]:
    """يرسل منتج اختبار جديد."""
    url = env.get("WEBHOOK_NEW_PRODUCTS", "")
    if not url:
        return {"success": False, "error": "WEBHOOK_NEW_PRODUCTS غير مهيّأ"}
    payload = {
        "data": [
            {
                "NO": "",
                "product_id": "TEST-NEW-001",
                "أسم المنتج": "منتج جديد اختبار — يمكنك حذفه",
                "سعر المنتج": 299,
                "رمز المنتج sku": "TEST-SKU-001",
                "الوزن": 1,
                "سعر التكلفة": 150,
                "السعر المخفض": 0,
                "الوصف": "هذا منتج اختبار من نظام مهووس للتسعير",
            }
        ]
    }
    return _test_webhook(url, payload)


def _load_send_log() -> pd.DataFrame:
    """يحمل سجل الإرسال من send_log."""
    try:
        from utils.send_log import load_send_log
        return load_send_log()
    except Exception:
        return pd.DataFrame()


def render(state: AppState, *, container: Optional[Any] = None) -> None:
    """يعرض صفحة أتمتة Make."""
    import streamlit as st

    from ui.components.page_header import render_page_header

    render_page_header("أتمتة Make", section="make")

    env = _read_env()

    # ── حالة Webhooks ──
    st.subheader("🔗 حالة Webhooks")

    c1, c2 = st.columns(2)
    with c1:
        update_url = env.get("WEBHOOK_UPDATE_PRICES", "")
        if update_url:
            st.success("✅ تحديث الأسعار: مهيّأ")
            st.caption(f"`{update_url[:60]}...`")
        else:
            st.warning("⚠️ تحديث الأسعار: غير مهيّأ")
            st.caption("اضبط `WEBHOOK_UPDATE_PRICES` في `.env`")

    with c2:
        new_url = env.get("WEBHOOK_NEW_PRODUCTS", "")
        if new_url:
            st.success("✅ منتجات جديدة: مهيّأ")
            st.caption(f"`{new_url[:60]}...`")
        else:
            st.warning("⚠️ منتجات جديدة: غير مهيّأ")
            st.caption("اضبط `WEBHOOK_NEW_PRODUCTS` في `.env`")

    st.markdown("---")

    # ── اختبار الإرسال ──
    st.subheader("🧪 اختبار الإرسال")
    st.caption("أرسل منتج اختبار للتحقق من عمل الـ webhook")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("📤 اختبار تحديث الأسعار", use_container_width=True, key="test_update"):
            with st.spinner("جاري الإرسال..."):
                result = _send_test_update(env)
            if result.get("success"):
                st.success(f"✅ نجاح ({result.get('status_code', '—')})")
            else:
                st.error(f"❌ فشل: {result.get('error', '—')}")
            if result.get("body"):
                st.caption(f"رد: `{result['body'][:200]}`")

    with c2:
        if st.button("📤 اختبار منتج جديد", use_container_width=True, key="test_new"):
            with st.spinner("جاري الإرسال..."):
                result = _send_test_new(env)
            if result.get("success"):
                st.success(f"✅ نجاح ({result.get('status_code', '—')})")
            else:
                st.error(f"❌ فشل: {result.get('error', '—')}")
            if result.get("body"):
                st.caption(f"رد: `{result['body'][:200]}`")

    st.markdown("---")

    # ── سجل الإرسال ──
    st.subheader("📋 سجل الإرسال")
    log_df = _load_send_log()
    if log_df.empty:
        st.info("لا سجل إرسال بعد — أرسل منتجات لظهور السجل")
    else:
        st.dataframe(
            log_df.tail(50),
            use_container_width=True,
            hide_index=True,
            height=min(400, 38 * len(log_df.tail(50)) + 38),
        )
        st.caption(f"إجمالي: {len(log_df)} سجل — يُعرض آخر 50")
