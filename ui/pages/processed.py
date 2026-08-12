"""ui/pages/processed.py — ✅ تمت المعالجة (بطاقات احترافية + تراجع + ملخص AI).

يجمع كل منتج جرى عليه إجراء (تحديث سعر / إرسال مفقود / تجاهل) في مكان واحد مع
بطاقة احترافية (صورة + اسم + ماركة + حالة) وتفصيل التعديل ووقته، فتبقى صفحات
الأقسام مرتّبة. الدوال الخالصة قابلة للاختبار.
"""
from __future__ import annotations

import html as _html
from typing import Any, Optional

from ui.state_manager import AppState
from utils.data_helpers import first_image_url_string

_SUMMARY_SYSTEM = "أنت مدير عمليات متجر. لخّص الإجراءات في 3 نقاط محفّزة موجزة."
_KIND_ICON = {"price": "💰", "missing": "⚡", "hidden": "🚫"}
_KIND_LABEL = {"price": "🚀 أُرسل لـ Make", "missing": "⚡ إرسال سريع", "hidden": "🚫 تم التجاهل"}
_KIND_COLOR = {"price": "#10B981", "missing": "#0EA5E9", "hidden": "#9CA3AF"}


def processed_rows(state: AppState, limit: int = 50) -> list[str]:
    """قائمة معرّفات الأسعار المعالَجة (خالص، مقصوصة) — توافق رجعي."""
    return sorted(state.processed_price_skus)[:limit]


def processed_entries(state: AppState, limit: int = 200) -> list[dict]:
    """سجلّ موحّد للمعالَجة: السجلّ الغنيّ + أي عناصر قديمة في المجموعات بلا سجلّ. خالص.

    كل عنصر: ``{key, name, action, detail, kind, ts, row_data?}`` والأحدث أولاً. آمن من الفراغ.
    """
    entries = [dict(entry) for entry in state.processed_log]
    logged = {entry.get("key") for entry in entries}
    for sku in sorted(state.processed_price_skus):
        if sku not in logged:
            entries.append({"key": sku, "name": "", "action": "تحديث السعر",
                            "detail": "", "kind": "price", "ts": ""})
    for url in sorted(state.processed_missing_urls):
        if url not in logged:
            entries.append({"key": url, "name": "", "action": "إرسال (مفقود)",
                            "detail": "", "kind": "missing", "ts": ""})
    return entries[:limit]


def _undo(state: AppState, entry: dict) -> None:
    """يتراجع عن إجراء: يزيله من المجموعة المناسبة (حسب ``kind``) + السجلّ، ثم يثبّت اللقطة."""
    key = str(entry.get("key", "")).strip()
    kind = entry.get("kind", "")
    if kind == "hidden":
        state.unhide(key)
    elif kind == "missing":
        state.processed_missing_urls.discard(key)
    else:
        state.processed_price_skus.discard(key)
    state.remove_log(key)
    state.persist_results()


def _esc(val: Any) -> str:
    return "" if val is None else _html.escape(str(val), quote=True)


def _build_card_html(entry: dict) -> str:
    """يبني HTML بطاقة احترافية لمنتج معالَج (صورة + اسم + ماركة + حالة)."""
    kind = entry.get("kind", "")
    icon = _KIND_ICON.get(kind, "✅")
    label = _KIND_LABEL.get(kind, "✅ تمت المعالجة")
    color = _KIND_COLOR.get(kind, "#818CF8")
    name = _esc(entry.get("name") or entry.get("key") or "—")
    detail = _esc(entry.get("detail", ""))
    ts = _esc(entry.get("ts", ""))

    # بيانات بصرية (إن وُجدت). حماية: pd.DataFrame(entries) في render يملأ
    # خلايا row_data المفقودة بـ NaN (float، وهو truthy فلا يلتقطه `or {}`)
    # ⇒ نضمن أنه dict دائماً قبل .get (نفس صنف خطأ NaN من pandas).
    rd = entry.get("row_data")
    if not isinstance(rd, dict):
        rd = {}
    raw_img = str(rd.get("image", "") or "")
    # تنظيف: استخراج أول رابط HTTP صالح (يعالج nan/None/عدة روابط)
    image_url = first_image_url_string(raw_img) if raw_img and raw_img.lower() not in ("nan", "none", "") else ""
    brand = _esc(rd.get("brand", ""))
    price = rd.get("price")
    price_str = f"{float(price):,.0f} ر.س" if price and float(price) > 0 else ""

    # صورة المنتج (أو أيقونة افتراضية)
    if image_url and image_url.lower().startswith(("http://", "https://", "//")):
        img_html = (
            f'<div style="width:72px;height:72px;border-radius:10px;overflow:hidden;'
            f'border:2px solid {color}33;flex-shrink:0;background:#0f172a;'
            f'display:flex;align-items:center;justify-content:center">'
            f'<img src="{_esc(image_url)}" loading="lazy" referrerpolicy="no-referrer" '
            f'style="max-width:100%;max-height:100%;object-fit:contain" '
            f'onerror="this.parentElement.innerHTML=\'🧴\';'
            f"this.parentElement.style.fontSize='28px';\"  /></div>"
        )
    else:
        img_html = (
            f'<div style="width:72px;height:72px;border-radius:10px;'
            f'border:2px solid {color}33;flex-shrink:0;background:#0f172a;'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:28px;color:#475569">{icon}</div>'
        )

    # سطر الماركة والسعر
    meta_bits = []
    if brand:
        meta_bits.append(f"🏷️ {brand}")
    if price_str:
        meta_bits.append(f"💰 {price_str}")
    meta_line = f'<div style="font-size:.76rem;color:#94A3B8;margin-top:2px">{" · ".join(meta_bits)}</div>' if meta_bits else ""

    # شارة الحالة
    badge = (
        f'<span style="font-size:.7rem;font-weight:700;color:{color};'
        f'background:{color}1A;border:1px solid {color}44;'
        f'border-radius:8px;padding:2px 9px;white-space:nowrap">{label}</span>'
    )

    # سطر التفصيل والوقت
    info_bits = []
    if detail:
        info_bits.append(detail)
    if ts:
        info_bits.append(f"🕐 {ts}")
    info_line = f'<div style="font-size:.72rem;color:#64748B;margin-top:3px">{" · ".join(info_bits)}</div>' if info_bits else ""

    # رابط المنتج (يفتح في تبويب جديد)
    our_url = str(rd.get("our_link", "")).strip()
    comp_url = str(rd.get("comp_link", "")).strip()
    # الأولوية: رابط منتجنا للأقسام السعرية، رابط المنافس للمفقودات
    link_url = our_url if our_url.lower().startswith("http") else (
        comp_url if comp_url.lower().startswith("http") else ""
    )
    if link_url:
        name_html = (
            f'<a href="{_esc(link_url)}" target="_blank" rel="noopener" '
            f'style="font-size:1rem;font-weight:700;color:#E2E8F0;text-decoration:none;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%;'
            f'display:inline-block">{name} 🔗</a>'
        )
    else:
        name_html = (
            f'<span style="font-size:1rem;font-weight:700;color:#E2E8F0;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">{name}</span>'
        )

    return (
        f'<div style="display:flex;align-items:flex-start;gap:12px;padding:12px 14px;'
        f'background:linear-gradient(180deg,#0f172a,#0e1118);border:1px solid #1F2937;'
        f'border-top:3px solid {color};border-radius:14px;margin:8px 0;'
        f'direction:rtl;font-family:Tajawal,sans-serif;'
        f'transition:transform .15s,box-shadow .15s" class="mhw-processed-card">'
        f'{img_html}'
        f'<div style="flex:1;min-width:0">'
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
        f'{name_html}'
        f'{badge}'
        f'</div>'
        f'{meta_line}{info_line}'
        f'</div>'
        f'</div>'
    )


def _render_summary(state: AppState, ai_service: Optional[Any]) -> None:
    """ملخص إداري عبر AI (اختياري)."""
    import streamlit as st

    entries = processed_entries(state, 30)
    if ai_service is None or not entries:
        return
    if st.button("📝 ملخص AI للإجراءات"):
        actions = "، ".join(
            f"{entry['action']}: {entry.get('name') or entry.get('key')}"
            for entry in entries
        )
        result = ai_service.call("الإجراءات المنفّذة: " + actions, _SUMMARY_SYSTEM)
        (st.info if result.success else st.warning)(
            result.response or "تعذّر توليد الملخص")


def render(state: AppState, *, ai_service: Optional[Any] = None) -> None:
    """يعرض صفحة المعالَجة كاملة (بطاقات احترافية + تراجع)."""
    import streamlit as st

    from ui.components.page_header import render_page_header, section_accent

    entries = processed_entries(state, limit=1_000_000)  # الكل (الترقيم يتولّى العرض)
    render_page_header("تمت المعالجة", section="processed", count=len(entries))
    if not entries:
        st.info("لا منتجات معالَجة بعد")
        return

    # ── شريط إحصائيات لاصق ──
    n_price = sum(1 for e in entries if e.get("kind") == "price")
    n_missing = sum(1 for e in entries if e.get("kind") == "missing")
    n_hidden = sum(1 for e in entries if e.get("kind") == "hidden")
    accent = section_accent("processed")
    st.markdown(
        f'<div class="mhw-ctrl" style="--ac:{accent}">'
        f'<div class="st-row">'
        f'<span class="st-i">📊 <b>{len(entries):,}</b> إجراء</span>'
        f'<span class="st-i">💰 <b>{n_price:,}</b> تحديث سعر</span>'
        f'<span class="st-i">⚡ <b>{n_missing:,}</b> إرسال مفقود</span>'
        f'<span class="st-i">🚫 <b>{n_hidden:,}</b> تم تجاهله</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── ترقيم: لا سقف 200 — كل المعالَجة تظهر مقسّمة على صفحات ──
    import pandas as pd
    from ui.components.pagination import paginate, render_pagination

    page = int(st.session_state.get("processed_page", 1))
    view = paginate(pd.DataFrame(entries), page, per_page=20)
    st.caption(view.caption)
    for _, (_i, row) in enumerate(view.items.iterrows()):
        entry = row.to_dict()
        st.markdown(_build_card_html(entry), unsafe_allow_html=True)
        # مفتاح ثابت بالمعرّف (يمنع تضارب الأزرار بين الصفحات)
        st.button(
            "↩️ تراجع", key=f"undo_{entry.get('key', _i)}",
            on_click=_undo, args=(state, entry),
        )
    render_pagination(view, "processed")
    _render_summary(state, ai_service)
