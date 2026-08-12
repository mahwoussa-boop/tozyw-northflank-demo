"""ui/pages/trash_bin.py — 🗑️ سلة المحذوفات (استرداد المنتجات المخفية).

تعرض كل المنتجات التي تم إخفاؤها (حذف ناعم) و تسمح باستردادها.
"""
from __future__ import annotations

import html as _html
from typing import Any, Optional

import pandas as pd

from services.decision_journal import record_decision
from ui.state_manager import AppState


_KIND_ICON = {"price": "💰", "missing": "⚡", "hidden": "🚫"}
_KIND_LABEL = {"price": "تحديث سعر", "missing": "إرسال مفقود", "hidden": "مخفي"}
_KIND_COLOR = {"price": "#10B981", "missing": "#0EA5E9", "hidden": "#9CA3AF"}


def _esc(val: Any) -> str:
    return "" if val is None else _html.escape(str(val), quote=True)


def _build_card_html(entry: dict) -> str:
    """يبني HTML بطاقة احترافية لمنتج مخفي."""
    kind = entry.get("kind", "hidden")
    icon = _KIND_ICON.get(kind, "🚫")
    label = _KIND_LABEL.get(kind, "مخفي")
    color = _KIND_COLOR.get(kind, "#9CA3AF")
    name = _esc(entry.get("name") or entry.get("key", "—"))
    detail = _esc(entry.get("detail", ""))
    ts = _esc(entry.get("ts", ""))

    rd = entry.get("row_data")
    if not isinstance(rd, dict):
        rd = {}
    raw_img = str(rd.get("image", "") or "")
    image_url = (
        raw_img
        if raw_img and raw_img.lower() not in ("nan", "none", "")
        else ""
    )
    brand = _esc(rd.get("brand", ""))
    price = rd.get("price")
    price_str = f"{float(price):,.0f} ر.س" if price and float(price) > 0 else ""

    if image_url and image_url.lower().startswith(("http://", "https://", "//")):
        img_html = (
            f'<div style="width:72px;height:72px;border-radius:10px;overflow:hidden;'
            f'border:2px solid {color}33;flex-shrink:0;background:#0f172a;'
            f'display:flex;align-items:center;justify-content:center">'
            f'<img src="{_esc(image_url)}" loading="lazy" referrerpolicy="no-referrer" '
            f'style="max-width:100%;max-height:100%;object-fit:contain" '
            f'onerror="this.parentElement.innerHTML=\'🧴\';'
            f"this.parentElement.style.fontSize='28px';\" /></div>"
        )
    else:
        img_html = (
            f'<div style="width:72px;height:72px;border-radius:10px;'
            f'border:2px solid {color}33;flex-shrink:0;background:#0f172a;'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:28px;color:#475569">{icon}</div>'
        )

    meta_bits = []
    if brand:
        meta_bits.append(f"🏷️ {brand}")
    if price_str:
        meta_bits.append(f"💰 {price_str}")
    meta_line = (
        f'<div style="font-size:.76rem;color:#94A3B8;margin-top:2px">{" · ".join(meta_bits)}</div>'
        if meta_bits
        else ""
    )

    info_bits = []
    if detail:
        info_bits.append(detail)
    if ts:
        info_bits.append(f"🕐 {ts}")
    info_line = (
        f'<div style="font-size:.72rem;color:#64748B;margin-top:3px">{" · ".join(info_bits)}</div>'
        if info_bits
        else ""
    )

    return (
        f'<div style="display:flex;align-items:flex-start;gap:12px;padding:12px 14px;'
        f'background:linear-gradient(180deg,#0f172a,#0e1118);border:1px solid #1F2937;'
        f'border-top:3px solid {color};border-radius:14px;margin:8px 0;'
        f'direction:rtl;font-family:Tajawal,sans-serif;'
        f'transition:transform .15s,box-shadow .15s" class="mhw-trash-card">'
        f'{img_html}'
        f'<div style="flex:1;min-width:0">'
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
        f'<span style="font-size:1rem;font-weight:700;color:#E2E8F0;'
        f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">{name}</span>'
        f'<span style="font-size:.7rem;font-weight:700;color:{color};'
        f'background:{color}1A;border:1px solid {color}44;'
        f'border-radius:8px;padding:2px 9px;white-space:nowrap">{label}</span>'
        f'</div>'
        f'{meta_line}{info_line}'
        f'</div>'
        f'</div>'
    )


def _recover(state: AppState, entry: dict) -> None:
    """يسترد منتجاً من الحذف الناعم."""
    key = str(entry.get("key", "")).strip()
    state.unhide(key)
    state.remove_log(key)
    state.processed_price_skus.discard(key)
    state.processed_missing_urls.discard(key)
    # تسجيل صامت: استرجاع (إشارة أن التجاهل السابق كان خطأ)
    record_decision("recover", str(entry.get("name") or key), "trash")
    state.persist_results()


def _recover_all(state: AppState) -> None:
    """يسترد كل المنتجات المخفية."""
    hidden_keys = list(state.hidden_products)
    for key in hidden_keys:
        state.unhide(key)
        state.remove_log(key)
        record_decision("recover", str(key), "trash", extra={"bulk": True})
    state.processed_price_skus.clear()
    state.processed_missing_urls.clear()
    state.persist_results()


def render(state: AppState, *, container: Optional[Any] = None) -> None:
    """يعرض صفحة سلة المحذوفات."""
    import streamlit as st

    from ui.components.page_header import render_page_header
    from ui.components.pagination import paginate, render_pagination

    render_page_header("سلة المحذوفات", section="trash")

    # ── جمع المنتجات المخفية ──
    hidden_entries: list[dict] = []
    for entry in state.processed_log:
        if entry.get("kind") == "hidden":
            hidden_entries.append(dict(entry))

    # أيضاً المنتجات في hidden_products بدون سجل
    logged_hidden = {e.get("key") for e in hidden_entries}
    for key in state.hidden_products:
        if key not in logged_hidden:
            hidden_entries.append({
                "key": key, "name": key.replace("softdel_", ""),
                "action": "تم التجاهل", "detail": "", "kind": "hidden",
                "ts": "", "row_data": {},
            })

    if not hidden_entries:
        st.success("✅ سلة المحذوفات فارغة — لا منتجات مخفية")
        return

    # ── شريط إحصائيات ──
    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;direction:rtl;'
        f'font-family:Tajawal,sans-serif;margin:8px 0 16px">'
        f'<div style="flex:1;min-width:160px;background:rgba(22,22,26,.92);'
        f'border:1px solid #1F2937;border-top:3px solid #EF4444;'
        f'border-radius:12px;padding:14px 16px;text-align:center">'
        f'<div style="font-size:.78rem;color:#94A3B8;margin-bottom:4px">🗑️ منتجات مخفية</div>'
        f'<div style="font-size:2rem;font-weight:900;color:#E2E8F0;line-height:1.1">'
        f'{len(hidden_entries):,}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── أزرار الإجراءات ──
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        if st.button("♻️ استرداد الكل", type="primary", use_container_width=True, key="recover_all"):
            _recover_all(state)
            st.success(f"✅ تم استرداد {len(hidden_entries)} منتج")
            st.rerun()

    st.markdown("---")

    # ── عرض المنتجات مع ترقيم ──
    page = int(st.session_state.get("trash_page", 1))
    view = paginate(pd.DataFrame(hidden_entries), page, per_page=20)
    st.caption(view.caption)

    for _, (_i, row) in enumerate(view.items.iterrows()):
        entry = row.to_dict()
        st.markdown(_build_card_html(entry), unsafe_allow_html=True)
        key = entry.get("key", _i)
        st.button(
            "♻️ استرداد", key=f"recover_{key}",
            on_click=_recover, args=(state, entry),
        )

    render_pagination(view, "trash")
