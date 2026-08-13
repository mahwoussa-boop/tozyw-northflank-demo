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


def _ai_diagnostic_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """يبسّط تقرير التشخيص للعرض دون مفاتيح أو نصوص استجابة حساسة."""
    rows: list[dict[str, Any]] = []
    for item in report.get("gemini") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "المزوّد": "Gemini",
            "المفتاح": item.get("key", "—"),
            "الحالة": str(item.get("status") or "—"),
            "HTTP": item.get("status_code") or "—",
        })
    for provider in ("openrouter", "cohere"):
        rows.append({
            "المزوّد": provider.title(),
            "المفتاح": "—",
            "الحالة": str(report.get(provider) or "⚠️ غير مهيأ"),
            "HTTP": "—",
        })
    return rows


def _render_ai_diagnostic() -> None:
    """يشغّل طلباً اختبارياً قصيراً لكل مزوّد، بلا كتابة أو كشف أسرار."""
    import streamlit as st

    st.markdown("---")
    st.subheader("🩺 فحص اتصال مزودي الذكاء الاصطناعي")
    st.caption(
        "فحص قراءة-فقط: يرسل طلباً اختبارياً قصيراً للمزوّدات المهيأة، "
        "ولا يعرض المفاتيح أو نصوص الاستجابة."
    )
    if not st.button("تشغيل فحص الذكاء الاصطناعي", key="ai_provider_diagnostic"):
        return

    try:
        from engines.ai_engine import diagnose_ai_providers

        with st.spinner("يُفحص اتصال Gemini وOpenRouter وCohere…"):
            report = diagnose_ai_providers()
    except Exception:
        st.error("تعذّر تشغيل فحص مزودي الذكاء الاصطناعي. راجع سجل الخدمة.")
        return

    rows = _ai_diagnostic_rows(report)
    st.dataframe(rows, hide_index=True, use_container_width=True)
    for recommendation in report.get("recommendations") or []:
        st.info(str(recommendation))
    if not any("✅" in str(row["الحالة"]) for row in rows):
        st.warning("لا يوجد مزوّد ذكاء اصطناعي يستجيب حالياً. راجع الإعدادات أو حدود الاستخدام.")


def _ai_live_smoke_summary(result: dict[str, Any]) -> dict[str, str]:
    """يلخّص اختبار المطابقة الحي دون عرض محتوى الاستجابة أو أي أسرار."""
    if bool(result.get("success")):
        return {
            "الحالة": "✅ استجابة حية ناجحة",
            "المزوّد": str(result.get("source") or "—"),
            "العينة": "مطابقة SKU بحجم وتركيز محددين",
        }
    return {
        "الحالة": "❌ لم تصل استجابة حية",
        "المزوّد": str(result.get("source") or "—"),
        "العينة": "مطابقة SKU بحجم وتركيز محددين",
    }


def _render_ai_live_smoke() -> None:
    """ينفذ عينة تحليل صغيرة عبر المسار الفعلي للمزوّد من دون كتابة نتائج."""
    import streamlit as st

    st.markdown("---")
    st.subheader("🔬 عينة تحليل ومطابقة حية")
    st.caption(
        "طلب واحد صغير يختبر تحليل الذكاء الاصطناعي لمنطق مطابقة SKU، "
        "ولا يقرأ قاعدة البيانات أو يكتب أي نتيجة أو يعرض نص استجابة المزوّد."
    )
    if not st.button("تشغيل العينة الحية", key="ai_live_matching_smoke"):
        return

    prompt = (
        "اختبار داخلي قصير لمنطق مطابقة SKU. قارن الحالتين فقط: "
        "(1) Dior Sauvage EDP 100ml مقابل Dior Sauvage Eau de Parfum 100 ml؛ "
        "(2) Dior Sauvage EDP 100ml مقابل Dior Sauvage EDP 50ml. "
        "اذكر في سطرين أن الأولى مطابقة والثانية غير مطابقة بسبب اختلاف الحجم."
    )
    try:
        from engines.ai_engine import call_ai

        with st.spinner("يُرسل طلب تحليل واحد قصير…"):
            result = call_ai(prompt, page="review")
    except Exception:
        st.error("تعذّر تشغيل العينة الحية. راجع سجل الخدمة.")
        return

    summary = _ai_live_smoke_summary(result if isinstance(result, dict) else {})
    if str(summary["الحالة"]).startswith("✅"):
        st.success("نجح مسار التحليل والمطابقة الحي.")
    else:
        st.error("لم ينجح مسار التحليل الحي. راجع حالة المزوّدات.")
    st.dataframe([summary], hide_index=True, use_container_width=True)


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

    _render_ai_diagnostic()
    _render_ai_live_smoke()

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
