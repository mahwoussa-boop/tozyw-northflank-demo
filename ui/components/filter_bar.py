"""ui/components/filter_bar.py — شريط فلاتر (تطبيق خالص + عرض رفيع).

``apply_filters`` خالصة على DataFrame وقابلة للاختبار؛ ``render_filter_bar``
تجمع القيم من موسّع مطويّ افتراضياً وتعيد ``Filters`` (بحث/ماركة/جنس/سعر/تطابق).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from conf.constants import (
    COL_BRAND,
    COL_COMP_COUNT,
    COL_COMP_LINK,
    COL_GENDER,
    COL_MATCH_RATIO,
    COL_OUR_NAME,
    COL_OUR_PRICE,
)
from ui.components.criticality import CRIT_CHOICES

_ALL = "الكل"

# عمود الخطورة من مخرجات محرك التسعير (عمود عرض، غير موجود في conf.constants).
# #PRESERVED_LOGIC: engines.engine — الخطورة = تصنيف خطورة المنافسة (🟢/🟡/🔴).
_COL_RISK = "الخطورة"


@dataclass(frozen=True)
class Filters:
    """قيم الفلاتر النشطة."""

    search: str = ""
    brand: str = _ALL
    gender: str = _ALL
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_match: Optional[float] = None
    min_competitors: Optional[int] = None
    risk: str = _ALL
    criticality: str = _ALL
    availability: str = _ALL   # الكل / 🟢 متوفر / 🔴 نفذت

    @property
    def active_chips(self) -> list[str]:
        """رقائق الفلاتر النشطة للعرض الشفّاف."""
        chips: list[str] = []
        if self.search:
            chips.append(f"🔎 {self.search}")
        if self.brand != _ALL:
            chips.append(f"🏷️ {self.brand}")
        if self.gender != _ALL:
            chips.append(f"🚻 {self.gender}")
        if self.availability != _ALL:
            chips.append(self.availability)
        if self.criticality != _ALL:
            chips.append(f"🚨 {self.criticality}")
        if self.min_price is not None or self.max_price is not None:
            chips.append(f"💵 {self.min_price or 0:.0f}–{self.max_price or '∞'}")
        if self.min_match is not None:
            chips.append(f"🎯 ≥{self.min_match:.0f}%")
        if self.min_competitors is not None:
            chips.append(f"👥 ≥{self.min_competitors}")
        if self.risk != _ALL:
            chips.append(f"⚠️ {self.risk}")
        return chips


def numeric_bounds(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """حدود (min, max) لعمود رقمي بأمان — (0,0) عند غياب/خواء العمود. خالص."""
    if df is None or df.empty or col not in df.columns:
        return (0.0, 0.0)
    series = pd.to_numeric(df[col], errors="coerce").dropna()
    if series.empty:
        return (0.0, 0.0)
    return (float(series.min()), float(series.max()))


def apply_filters(df: pd.DataFrame, filters: Filters,
                  name_col: str = COL_OUR_NAME) -> pd.DataFrame:
    """يطبّق الفلاتر على DataFrame (خالص، vectorized، غياب العمود = لا فلترة)."""
    if df is None or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if filters.search and name_col in df.columns:
        mask &= df[name_col].astype(str).str.contains(
            filters.search, case=False, na=False, regex=False)
    if filters.brand != _ALL and COL_BRAND in df.columns:
        mask &= df[COL_BRAND].astype(str).str.strip() == filters.brand
    if filters.gender != _ALL and COL_GENDER in df.columns:
        mask &= df[COL_GENDER].astype(str).str.strip() == filters.gender
    if (filters.min_price is not None or filters.max_price is not None) \
            and COL_OUR_PRICE in df.columns:
        price = pd.to_numeric(df[COL_OUR_PRICE], errors="coerce").fillna(0)
        if filters.min_price is not None:
            mask &= price >= filters.min_price
        if filters.max_price is not None:
            mask &= price <= filters.max_price
    if filters.min_match is not None and COL_MATCH_RATIO in df.columns:
        ratio = pd.to_numeric(df[COL_MATCH_RATIO], errors="coerce").fillna(0)
        mask &= ratio >= filters.min_match
    if filters.min_competitors is not None and COL_COMP_COUNT in df.columns:
        count = pd.to_numeric(df[COL_COMP_COUNT], errors="coerce").fillna(0)
        mask &= count >= filters.min_competitors
    if filters.risk != _ALL and _COL_RISK in df.columns:
        mask &= df[_COL_RISK].astype(str).str.strip() == filters.risk
    return df[mask]


def filter_by_availability(
    df: pd.DataFrame, choice: str, oos_links: Any,
    link_col: str = COL_COMP_LINK,
) -> pd.DataFrame:
    """يفلتر صفوف القسم بتوفّر المنافس (vectorized). خالص وقابل للاختبار.

    ``oos_links``: مجموعة روابط المنتجات النافذة (availability=0). صفّ نافذ إن كان
    رابط منافسه ضمنها. ``choice`` = «🔴 نفذت» يُبقي النافذة، «🟢 متوفر» يُبقي الباقي.
    """
    if (df is None or df.empty or choice == _ALL
            or link_col not in df.columns or not oos_links):
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    is_oos = df[link_col].astype(str).str.strip().isin(set(oos_links))
    return df[is_oos] if "نفذ" in choice else df[~is_oos]


def options_for(df: pd.DataFrame, col: str) -> list[str]:
    """قيم فريدة لعمود (لقوائم الاختيار) مع «الكل» أولاً."""
    if df is None or df.empty or col not in df.columns:
        return [_ALL]
    values = sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()})
    return [_ALL, *values]


def _price_slider(st: Any, df: pd.DataFrame, key: str) -> tuple[Optional[float], Optional[float]]:
    """منزلق نطاق السعر — يعيد (None, None) إن غاب العمود أو غُطّي المدى كله."""
    lo, hi = numeric_bounds(df, COL_OUR_PRICE)
    if hi <= lo:
        return None, None
    low, high = st.slider(
        "نطاق السعر (ر.س)", min_value=lo, max_value=hi,
        value=(lo, hi), key=f"{key}_price",
    )
    return (None, None) if (low <= lo and high >= hi) else (float(low), float(high))


def _match_slider(st: Any, df: pd.DataFrame, key: str) -> Optional[float]:
    """منزلق أدنى نسبة تطابق — None إن غاب العمود أو القيمة صفر (لا فلترة)."""
    if COL_MATCH_RATIO not in df.columns:
        return None
    value = st.slider("أدنى نسبة تطابق %", 0, 100, 0, key=f"{key}_match")
    return None if value <= 0 else float(value)


def _competitors_slider(st: Any, df: pd.DataFrame, key: str) -> Optional[int]:
    """منزلق أدنى عدد منافسين — None إن غاب العمود أو القيمة صفر (لا فلترة)."""
    _, hi = numeric_bounds(df, COL_COMP_COUNT)
    if hi <= 0:
        return None
    value = st.slider("أدنى عدد منافسين", 0, int(hi), 0, key=f"{key}_comp")
    return None if value <= 0 else int(value)


def _risk_select(st: Any, df: pd.DataFrame, key: str) -> str:
    """قائمة اختيار الخطورة — «الكل» إن غاب العمود (لا فلترة)."""
    if _COL_RISK not in df.columns:
        return _ALL
    return st.selectbox("الخطورة", options_for(df, _COL_RISK), key=f"{key}_risk")


def _advanced_chips(f: Filters) -> list[str]:
    """شرائح الفلاتر المتقدمة النشطة فقط (المخفية في الـ Popover)."""
    chips: list[str] = []
    if f.min_price is not None or f.max_price is not None:
        chips.append(f"💵 {f.min_price or 0:.0f}–{f.max_price or '∞'}")
    if f.min_match is not None:
        chips.append(f"🎯 ≥{f.min_match:.0f}%")
    if f.min_competitors is not None:
        chips.append(f"👥 ≥{f.min_competitors}")
    if f.risk != _ALL:
        chips.append(f"⚠️ {f.risk}")
    return chips


def render_filter_bar(df: pd.DataFrame, key: str) -> Filters:
    """يعرض شريط فلاتر مدمج أفقي + popover عائم للفلاتر المتقدمة (بلا Rerun حتى «تطبيق»)."""
    import html as _html
    import streamlit as st

    # ── صف الفلاتر السريعة (بحث + ماركة + جنس + التوفّر + حرجية) ──
    fc1, fc2, fc3, fc4, fc5 = st.columns([4, 3, 3, 3, 3])
    search = fc1.text_input(
        "بحث", placeholder="🔎 بحث بالاسم…",
        key=f"{key}_q", label_visibility="collapsed",
    )
    brand = fc2.selectbox(
        "🏷️ الماركة", options_for(df, COL_BRAND), key=f"{key}_b",
    )
    gender = fc3.selectbox(
        "🚻 الجنس", options_for(df, COL_GENDER), key=f"{key}_g",
    )
    # فلتر التوفّر (متوفر/نفذت لدى المنافس) — يعمل عبر مجموعة الروابط النافذة.
    availability = fc4.selectbox(
        "🟢 التوفّر", [_ALL, "🟢 متوفر", "🔴 نفذت"], key=f"{key}_avail",
        help="🔴 نفذت = منافس هذا المنتج سُحب/علّمه المتجر غير متوفر.",
    )
    # فلتر الأهمية القصوى (بارز — حرج/متوسط/عادي) من القائمة مباشرةً.
    criticality = fc5.selectbox(
        "🚨 الأهمية", CRIT_CHOICES, key=f"{key}_crit",
        help="حرج = فجوة سعر ضخمة أو نفاد مخزون المنافس أو خطورة محرك عالية.",
    )

    # ── فلاتر متقدمة: popover عائم + form (بلا Rerun حتى الضغط على «تطبيق») ──
    min_price = max_price = min_match = min_competitors = None
    risk = _ALL
    with st.popover("⚙️ فلاتر متقدمة"):
        with st.form(key=f"{key}_adv", border=False):
            min_price, max_price = _price_slider(st, df, key)
            min_match = _match_slider(st, df, key)
            min_competitors = _competitors_slider(st, df, key)
            risk = _risk_select(st, df, key)
            st.form_submit_button(
                "✅ تطبيق الفلاتر", use_container_width=True,
            )

    filters = Filters(
        search=search.strip(), brand=brand, gender=gender,
        min_price=min_price, max_price=max_price, min_match=min_match,
        min_competitors=min_competitors, risk=risk, criticality=criticality,
        availability=availability,
    )

    # ── شرائح الفلاتر المتقدمة النشطة (خارج الـ Popover — يراها المستخدم دائماً) ──
    adv = _advanced_chips(filters)
    if adv:
        chips = " ".join(
            f'<span class="mhw-fchip">{_html.escape(str(c))}</span>'
            for c in adv
        )
        st.markdown(
            f'<div class="mhw-fchips">{chips}</div>',
            unsafe_allow_html=True,
        )

    return filters

