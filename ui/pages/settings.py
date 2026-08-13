"""ui/pages/settings.py — صفحة الإعدادات (⚙️ الإعدادات).

تعرض و تسمح بتعديل الإعدادات الرئيسية للتطبيق:
- مفاتيح API (Gemini, OpenRouter, Cohere)
- عتبات التسعير والمطابقة
- إعدادات Webhook (Make)
- إعدادات عامة
"""
from __future__ import annotations

from typing import Any, Optional

from ui.state_manager import AppState


def _image_env_path():
    """‏.env المرفق بالصورة — قيم افتراضية تُقرأ ولا يُكتب إليها في النشر."""
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / ".env"


def _persistent_env_path():
    """‏.env على الحجم الدائم، أو نفس ملف الصورة عند التطوير المحلي.

    جذر التطبيق داخل الحاوية (``/app``) يُستبدل مع كل نشر، فالكتابة إليه تعني
    ضياع إعدادات المالك في كل مرة. ``DATA_DIR`` هو ما يبقى (``/data`` على
    Northflank)، فهو موضع الحفظ متى كان مضبوطاً.
    """
    import os
    from pathlib import Path
    data_dir = (os.environ.get("DATA_DIR") or "").strip()
    return Path(data_dir) / ".env" if data_dir else _image_env_path()


def _parse_env_file(path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _read_env() -> dict[str, str]:
    """قيم الصورة أولاً، ثم يعلوها ما حفظه المالك على الحجم الدائم."""
    env = _parse_env_file(_image_env_path())
    persistent = _persistent_env_path()
    if persistent != _image_env_path():
        env.update(_parse_env_file(persistent))
    return env


def _save_env(updates: dict[str, str]) -> bool:
    """يحفّظ التحديثات في ‎.env الدائم (على الحجم متى وُجد)."""
    import os
    env_file = _persistent_env_path()
    try:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()
        # تحديث السطور الموجودة أو إضافة جديدة
        existing_keys = set()
        for i, line in enumerate(lines):
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                k = line.split("=", 1)[0].strip()
                if k in updates:
                    lines[i] = f'{k}="{updates[k]}"'
                    existing_keys.add(k)
        for k, v in updates.items():
            if k not in existing_keys:
                lines.append(f'{k}="{v}"')
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # تحديث os.environ للجلسة الحالية
        for k, v in updates.items():
            os.environ[k] = v
        return True
    except Exception:
        return False


def _render_api_keys(env: dict[str, str]) -> dict[str, str]:
    """قسم مفاتيح API."""
    import streamlit as st

    st.subheader("🔑 مفاتيح API")
    st.caption("تُخزَّن في ملف `.env` — لا تُرفع أبداً إلى git")

    updates: dict[str, str] = {}

    c1, c2 = st.columns(2)
    with c1:
        gemini = st.text_input(
            "GEMINI_API_KEY",
            value=env.get("GEMINI_API_KEY", ""),
            type="password",
            help="مفتاح Gemini لـ AI Router وإعادة التوزيع",
        )
        if gemini:
            updates["GEMINI_API_KEY"] = gemini

        openrouter = st.text_input(
            "OPENROUTER_API_KEY",
            value=env.get("OPENROUTER_API_KEY", ""),
            type="password",
            help="مفتاح OpenRouter (الأولوية الأولى للذكاء الاصطناعي)",
        )
        if openrouter:
            updates["OPENROUTER_API_KEY"] = openrouter

    with c2:
        cohere = st.text_input(
            "COHERE_API_KEY",
            value=env.get("COHERE_API_KEY", ""),
            type="password",
            help="مفتاح Cohere (احتياطي)",
        )
        if cohere:
            updates["COHERE_API_KEY"] = cohere

        extra = st.text_input(
            "EXTRA_API_KEY",
            value=env.get("EXTRA_API_KEY", ""),
            type="password",
            help="مفتاح إضافي",
        )
        if extra:
            updates["EXTRA_API_KEY"] = extra

    return updates


def _render_webhooks(env: dict[str, str]) -> dict[str, str]:
    """قسم Webhooks."""
    import streamlit as st

    st.subheader("🔗 Webhooks (Make.com)")
    st.caption("روابط الإرسال لمنصة Make")

    updates: dict[str, str] = {}

    c1, c2 = st.columns(2)
    with c1:
        webhook_update = st.text_input(
            "WEBHOOK_UPDATE_PRICES",
            value=env.get("WEBHOOK_UPDATE_PRICES", ""),
            help="رابط تحديث الأسعار في Make",
        )
        if webhook_update:
            updates["WEBHOOK_UPDATE_PRICES"] = webhook_update

    with c2:
        webhook_new = st.text_input(
            "WEBHOOK_NEW_PRODUCTS",
            value=env.get("WEBHOOK_NEW_PRODUCTS", ""),
            help="رابط إضافة المنتجات الجديدة في Make",
        )
        if webhook_new:
            updates["WEBHOOK_NEW_PRODUCTS"] = webhook_new

    return updates


def _render_general(env: dict[str, str]) -> dict[str, str]:
    """قسم عام."""
    import streamlit as st

    st.subheader("⚙️ عام")

    updates: dict[str, str] = {}

    c1, _c2 = st.columns(2)
    with c1:
        db_path = st.text_input(
            "DB_PATH",
            value=env.get("DB_PATH", ""),
            help="مسار قاعدة البيانات (فارغ = الافتراضي)",
        )
        if db_path:
            updates["DB_PATH"] = db_path

    return updates


def render(state: AppState, *, container: Optional[Any] = None) -> None:
    """يعرض صفحة الإعدادات كاملة."""
    import streamlit as st

    from ui.components.page_header import render_page_header

    render_page_header("الإعدادات", section="settings")

    env = _read_env()
    all_updates: dict[str, str] = {}

    with st.form("settings_form"):
        api_updates = _render_api_keys(env)
        webhook_updates = _render_webhooks(env)
        general_updates = _render_general(env)

        all_updates.update(api_updates)
        all_updates.update(webhook_updates)
        all_updates.update(general_updates)

        st.markdown("---")
        submitted = st.form_submit_button("💾 حفظ الإعدادات", type="primary", use_container_width=True)

    if submitted:
        if all_updates:
            if _save_env(all_updates):
                # حاوية الاعتماديات مخزَّنة بـ``st.cache_resource`` وتُبنى مرّة واحدة
                # لكل خادم، فإعادة تحميل الصفحة وحدها **لا** تُعيد بناء خدمات الذكاء
                # بالمفاتيح الجديدة. تفريغ الكاش هنا يجعل الحفظ يسري فعلاً لا وعداً.
                st.cache_resource.clear()
                st.success(f"✅ حُفظت الإعدادات في {_persistent_env_path()} وسرت فوراً")
            else:
                st.error(
                    f"❌ تعذّرت الكتابة في {_persistent_env_path()} — تحقّق من الصلاحيات"
                )
        else:
            st.info("ℹ️ لم تُدخل أي تغييرات")

    # ── معلومات النظام ──
    st.markdown("---")
    st.subheader("📊 معلومات النظام")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        from config import APP_VERSION
        st.metric("الإصدار", APP_VERSION)
    with c2:
        import sys
        st.metric("بايثون", f"{sys.version_info.major}.{sys.version_info.minor}")
    with c3:
        import pandas as pd
        st.metric("pandas", pd.__version__)
    with c4:
        try:
            import streamlit as st_mod
            st.metric("Streamlit", st_mod.__version__)
        except Exception:
            st.metric("Streamlit", "—")

    # ── مسارات البيانات ──
    from conf.constants import DATA_DIR, COMPETITOR_DB_PATH, MISSING_CACHE_PATH
    st.caption(f"📁 بيانات: `{DATA_DIR}`")
    st.caption(f"🗄️ قاعدة المنافسين: `{COMPETITOR_DB_PATH}`")
    st.caption(f"💾 كاش المفقودات: `{MISSING_CACHE_PATH}`")
