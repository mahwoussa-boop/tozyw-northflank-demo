"""ui/components/salla_export.py — تصدير «سلة الشامل» للمفقودات (CSV بـ meta-header).

يلفّ ``utils.salla_shamel_export.export_to_salla_shamel_csv`` (يستورد Streamlit ⇒
استيراد كسول عند الطلب فقط، تماماً مثل ``ExportService.salla_dataframe``) لأنه
الوحيد الذي يضيف صفّ الـ meta-header (``بيانات المنتج``) الإلزامي لقالب سلة —
بخلاف ``salla_dataframe`` الذي يعيد DataFrame بلا CSV.

بوابة الجودة (``utils.product_gate``: وصف مهووس صالح + رابط صورة حقيقي) تُبقي
الصفوف الصالحة فقط؛ متحقَّق على بيانات حقيقية: 26173/26178 يمرّون (5 مُستبعَدون
لغياب صورة). الدالة الخالصة ``salla_export_summary`` تبني (bytes/مُصدَّر/مُستبعَد)
عبر باني محقون ⇒ قابلة للاختبار بلا أي استيراد utils أو نداء حقيقي.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd


def _default_builder(
    missing_df: pd.DataFrame,
    our_catalog_df: Optional[pd.DataFrame],
    verify_missing: bool,
) -> tuple:
    """الباني الافتراضي — استيراد كسول لـ utils (يستورد Streamlit داخلياً)."""
    from utils.salla_shamel_export import export_to_salla_shamel_csv  # كسول

    return export_to_salla_shamel_csv(
        missing_df, our_catalog_df=our_catalog_df, verify_missing=verify_missing,
    )


def salla_export_summary(
    missing_df: pd.DataFrame,
    our_catalog_df: Optional[pd.DataFrame] = None,
    *,
    verify_missing: bool = False,
    builder: Callable[..., tuple] = _default_builder,
) -> tuple[bytes, int, int]:
    """يبني (csv_bytes، عدد المُصدَّر، عدد المُستبعَد) لتصدير سلة الشامل (خالص).

    ``builder`` محقون ⇒ الاختبار لا يستدعي utils/Streamlit الحقيقي. آمن من
    df فارغ/None. المُستبعَد = صفوف المصدر − المُصدَّرة (تُسقطها بوابة الجودة).
    ``verify_missing=False`` لأن ``missing_df`` أصلاً مجموعة المفقودات المنقّاة.
    """
    n_source = len(missing_df) if isinstance(missing_df, pd.DataFrame) else 0
    if n_source == 0:
        return b"", 0, 0
    csv_bytes, exported, _found = builder(missing_df, our_catalog_df, verify_missing)
    exported = int(exported)
    excluded = max(n_source - exported, 0)
    data = bytes(csv_bytes) if isinstance(csv_bytes, (bytes, bytearray)) else b""
    return data, exported, excluded


def render_salla_export(
    missing_df: pd.DataFrame,
    our_catalog_df: Optional[pd.DataFrame] = None,
    *,
    key: str = "salla_shamel",
) -> None:
    """زرّ «📥 تصدير سلة الشامل» — يبني عند الطلب ثم يعرض زر تنزيل + إحصاء.

    يعمل على ``missing_df`` المُمرَّر (المفلتر حسب الثقة في الصفحة). يبني مرّة
    عند الضغط ويخزّن الناتج في الحالة (لا إعادة بناء عند كل rerun).
    """
    import streamlit as st

    n = len(missing_df) if isinstance(missing_df, pd.DataFrame) else 0
    if st.button(
        f"📥 تصدير سلة الشامل ({n:,})", key=f"{key}_btn",
        disabled=n == 0, use_container_width=True,
        help="ملف CSV بقالب سلة الرسمي (40 عمود + صفّ بيانات المنتج). "
             "تُستبعَد الصفوف بلا وصف صالح أو رابط صورة حقيقي.",
    ):
        with st.spinner("جاري بناء ملف سلة الشامل (قد يستغرق ثوانٍ)…"):
            try:
                data, exported, excluded = salla_export_summary(
                    missing_df, our_catalog_df,
                )
            except Exception as exc:  # noqa: BLE001 — لا انهيار للواجهة
                st.session_state.pop(f"{key}_data", None)
                st.error(f"⚠️ تعذّر بناء ملف سلة: {exc}")
                return
        st.session_state[f"{key}_data"] = (data, exported, excluded)

    ready = st.session_state.get(f"{key}_data")
    if not ready:
        return
    data, exported, excluded = ready
    if exported == 0:
        st.warning("لا صفوف صالحة للتصدير (استبعدتها بوابة الجودة).")
        return
    st.success(f"✅ جاهز: {exported:,} منتج مُصدَّر · {excluded:,} مُستبعَد")
    st.download_button(
        "⬇️ تنزيل ملف سلة (CSV)", data, "salla_shamel.csv", mime="text/csv",
        key=f"{key}_dl", use_container_width=True,
    )
