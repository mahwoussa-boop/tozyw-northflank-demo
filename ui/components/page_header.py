"""ui/components/page_header.py — رأس صفحة موحّد + فلاتر مدمجة ثابتة + ألوان الأقسام.

فكرة موحّدة لكل صفحات التطبيق: شريط علوي **ثابت (sticky)** مختصر أنيق يحمل
عنوان الصفحة + لون القسم + العدّاد + ملخّص الفلاتر النشطة. كل صفحة تأخذ لونها
الخاص (لتسهيل التعرّف والمقارنة واتخاذ القرار) من مصدر واحد: ``section_theme``.

النمط (كبقية المكوّنات): دوال نقية قابلة للاختبار + غلاف ``render_*`` يستورد
``st`` بكسل. الألوان مأخوذة من ``conf.constants.COLORS`` ومُوسَّعة بألوان مميِّزة
لكل قسم.
"""
from __future__ import annotations

import html
from typing import Any, Iterable, Mapping

# ── لون + أيقونة + اسم لكل قسم (مصدر واحد للحقيقة البصرية) ──
# المفاتيح = قيم SectionType.value + صفحات إضافية (dashboard/processed/scraper).
_SECTION_THEME: dict[str, dict[str, str]] = {
    "dashboard":   {"color": "#818CF8", "icon": "📊", "label": "لوحة التحكم"},
    "price_raise": {"color": "#EF4444", "icon": "🔴", "label": "رفع السعر"},
    "price_lower": {"color": "#10B981", "icon": "🟢", "label": "خفض السعر"},
    "approved":    {"color": "#22C55E", "icon": "✅", "label": "موافق عليها"},
    "review":      {"color": "#F59E0B", "icon": "⚠️", "label": "تحت المراجعة"},
    "excluded":    {"color": "#9CA3AF", "icon": "⚪", "label": "مستبعدة"},
    "missing":     {"color": "#0EA5E9", "icon": "🔍", "label": "منتجات مفقودة"},
    "processed":   {"color": "#A78BFA", "icon": "📦", "label": "تمت المعالجة"},
    "scraper":     {"color": "#F472B6", "icon": "🕷️", "label": "الكشّاف"},
    "new_at_comp": {"color": "#14B8A6", "icon": "🆕", "label": "جديد عند المنافسين"},
    "ai_copilot":  {"color": "#6366F1", "icon": "🤖", "label": "مساعد التسعير"},
    "redistribute":{"color": "#8B5CF6", "icon": "🔄", "label": "إعادة التوزيع"},
    "settings":    {"color": "#64748B", "icon": "⚙️", "label": "الإعدادات"},
    "trash":       {"color": "#EF4444", "icon": "🗑️", "label": "سلة المحذوفات"},
    "make":        {"color": "#F59E0B", "icon": "⚡", "label": "أتمتة Make"},
    "automation":  {"color": "#8B5CF6", "icon": "🔄", "label": "الأتمتة الذكية"},
    "factory":     {"color": "#EC4899", "icon": "✨", "label": "مصنع المنتجات"},
}
_DEFAULT_THEME: dict[str, str] = {"color": "#818CF8", "icon": "•", "label": "صفحة"}


def section_theme(section: Any) -> dict[str, str]:
    """يرجع ``{color, icon, label}`` للقسم (يقبل SectionType أو نصاً). آمن دائماً."""
    key = getattr(section, "value", section)
    return dict(_SECTION_THEME.get(str(key), _DEFAULT_THEME))


def section_accent(section: Any) -> str:
    """لون القسم فقط (اختصار شائع الاستخدام في البطاقات)."""
    return section_theme(section)["color"]


def _esc(value: Any) -> str:
    return "" if value is None else html.escape(str(value), quote=True)


def page_header_css() -> str:
    """أنماط الرأس الثابت + شريط التحكم اللاصق + الفلاتر المدمجة (تُحقن مرّة واحدة)."""
    return """<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;900&display=swap');
.mhw-hdr{position:sticky;top:0;z-index:99;background:linear-gradient(180deg,#0f0f12 0%,#0b0b0d 100%);direction:rtl;
  font-family:'Tajawal',sans-serif;display:flex;align-items:center;gap:10px;
  padding:10px 14px;margin:0 0 8px;border-radius:0 0 14px 14px;
  border:1px solid #1F2937;border-top:3px solid var(--ac,#818CF8);
  box-shadow:0 4px 20px rgba(0,0,0,.3)}
.mhw-hdr .ic{font-size:1.3rem;line-height:1}
.mhw-hdr .ttl{font-size:1.15rem;font-weight:900;color:#fafafa;white-space:nowrap;letter-spacing:-.3px}
.mhw-hdr .cnt{font-size:.85rem;font-weight:800;color:var(--ac,#818CF8);
  background:#16161a;border:1px solid #1F2937;border-radius:20px;padding:3px 12px}
.mhw-hdr .sub{font-size:.78rem;color:#94A3B8;margin-inline-start:auto;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55%}
.mhw-hdr .chip{display:inline-block;background:#16161a;border:1px solid #1F2937;
  border-radius:8px;padding:2px 10px;margin-inline-start:5px;color:#cbd5e1;font-size:.72rem}

/* ── شريط التحكم اللاصق (إحصائيات + شفافية — زجاجي) ── */
.mhw-ctrl{position:sticky;top:3.6rem;z-index:98;
  background:rgba(15,15,18,.95);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border:1px solid #1F2937;border-top:2px solid var(--ac,#818CF8);
  border-radius:0 0 12px 12px;padding:10px 16px 8px;
  margin:-2px 0 10px;direction:rtl;font-family:'Tajawal',sans-serif;
  box-shadow:0 2px 12px rgba(0,0,0,.2)}
.mhw-ctrl .st-row{display:flex;gap:20px;flex-wrap:wrap;align-items:center}
.mhw-ctrl .st-i{font-size:.84rem;color:#94A3B8;white-space:nowrap}
.mhw-ctrl .st-i b{font-weight:900;color:#E2E8F0;font-size:1.1rem}
.mhw-ctrl .st-tr{font-size:.74rem;color:#64748B;margin-inline-start:auto}

/* ── شرائح الفلاتر النشطة (ذهبية مميزة) ── */
.mhw-fchips{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 4px;direction:rtl;
  font-family:'Tajawal',sans-serif}
.mhw-fchip{font-size:.74rem;font-weight:700;color:#d4af37;
  background:rgba(212,175,55,.12);border:1px solid rgba(212,175,55,.35);
  border-radius:10px;padding:3px 12px;white-space:nowrap;
  display:inline-flex;align-items:center;gap:4px;
  transition:background .2s,border-color .2s,transform .15s}
.mhw-fchip:hover{background:rgba(212,175,55,.22);border-color:rgba(212,175,55,.6);transform:translateY(-1px)}

/* ── التصاق: إصلاح overflow للحاويات الأب في Streamlit ── */
[data-testid="stMainBlockContainer"]{overflow:visible!important}
[data-testid="stVerticalBlock"]:has(.mhw-ctrl),
[data-testid="stVerticalBlock"]:has(.mhw-hdr){overflow:visible!important}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mhw-ctrl),
[data-testid="stVerticalBlockBorderWrapper"]:has(.mhw-hdr){overflow:visible!important}
div.stMainBlockContainer{overflow:visible!important}

/* ── تجاوب الشريط اللاصق + الرأس على الهواتف ── */
@media (max-width:640px){
  .mhw-hdr{flex-wrap:wrap;gap:6px;padding:8px 12px}
  .mhw-hdr .ttl{font-size:1rem}
  .mhw-hdr .sub{max-width:100%;margin-inline-start:0}
  .mhw-ctrl{padding:8px 12px 6px}
  .mhw-ctrl .st-row{gap:12px}
  .mhw-ctrl .st-i{font-size:.76rem}
  .mhw-ctrl .st-i b{font-size:.98rem}
  .mhw-fchip{font-size:.68rem;padding:2px 8px}
}
@media (max-width:400px){
  .mhw-hdr .ttl{font-size:.92rem}
  .mhw-hdr .cnt{font-size:.75rem;padding:2px 8px}
}
</style>
"""



def build_page_header_html(
    title: str,
    *,
    section: Any = None,
    count: int | None = None,
    active_filters: Iterable[str] | None = None,
) -> str:
    """يبني HTML الرأس الثابت (نقي). ``active_filters`` = ملخّص قصير للفلاتر النشطة."""
    th = section_theme(section)
    cnt = (
        f'<span class="cnt">{int(count):,} عنصر</span>'
        if count is not None else ""
    )
    chips = "".join(
        f'<span class="chip">{_esc(c)}</span>' for c in (active_filters or [])
    )
    sub = f'<span class="sub">{chips}</span>' if chips else ""
    card = (
        f'<div class="mhw-hdr" style="--ac:{th["color"]}">'
        f'<span class="ic">{th["icon"]}</span>'
        f'<span class="ttl">{_esc(title)}</span>'
        f"{cnt}{sub}"
        "</div>"
    )
    return "".join(line for line in card.split("\n") if line.strip())


def render_page_header(
    title: str,
    *,
    section: Any = None,
    count: int | None = None,
    active_filters: Iterable[str] | None = None,
    inject_css: bool = True,
) -> dict[str, str]:
    """يرسم الرأس الثابت ويُرجع ثيمة القسم (لتلوين البطاقات بنفس اللون)."""
    import streamlit as st  # استيراد كسول

    if inject_css and not st.session_state.get("_mhw_hdr_css"):
        st.markdown(page_header_css(), unsafe_allow_html=True)
        st.session_state["_mhw_hdr_css"] = True

    st.markdown(
        build_page_header_html(
            title, section=section, count=count, active_filters=active_filters,
        ),
        unsafe_allow_html=True,
    )
    return section_theme(section)


def sidebar_nav_css() -> str:
    """يحوّل راديو الشريط الجانبي إلى أزرار متوسطة–كبيرة أنيقة (خفيفة، بلا تأثيرات ثقيلة).

    يبقي سلوك الراديو (حالة موثوقة، بلا إعادة تشغيل يدوية) ويغيّر الشكل فقط:
    كل خيار صار بطاقة-زرّاً عريضاً، والمختار بإطار ذهبي. متوافق مع streamlit 1.55.
    """
    return """<style>
section[data-testid="stSidebar"]{font-family:'Tajawal',sans-serif}
section[data-testid="stSidebar"] div[role="radiogroup"]{gap:8px;display:flex;flex-direction:column}
section[data-testid="stSidebar"] div[role="radiogroup"] > label{
  background:#16161a;border:1px solid #1F2937;border-radius:10px;
  padding:11px 13px;margin:0;width:100%;cursor:pointer;font-weight:700;
  font-size:1.02rem;transition:border-color .15s,background .15s}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover{
  border-color:#818CF8;background:#1b1b22}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked){
  border-color:#d4af37;background:rgba(212,175,55,.12)}
section[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child{display:none}
</style>
"""


def active_filter_summary(values: Mapping[str, Any]) -> list[str]:
    """يحوّل قيم الفلاتر إلى شرائح نصّية قصيرة (يتجاهل الفارغ/الكل). نقي."""
    chips: list[str] = []
    for label, val in values.items():
        if val in (None, "", "الكل", 0):
            continue
        chips.append(f"{label}: {val}")
    return chips
