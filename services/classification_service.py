"""services/classification_service.py — توزيع المنتجات على الأقسام (نقل _split_results).

نقل دقيق للجزء النقي من ``_split_results`` (app.py:392): توزيع حصري لكل منتج
على قسم واحد عبر بادئة عمود (القرار)، مع شبكة أمان تُلحق أي منتج غير مُوزَّع
بـ «مستبعد» (لا فقدان بيانات أبداً).

⚠️ كتلة «Smart Reversion» (app.py:410-462) تقرأ/تكتب ``st.session_state`` ⇒
هي شأن طبقة الحالة لا التصنيف النقي، فلا تُنقل هنا (تبقى الخدمة بلا Streamlit).

#PRESERVED_LOGIC: مطابق لمنطق التوزيع وشبكة الأمان وحارس الشفافية (app.py:464-513).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from conf.constants import (
    COL_DECISION,
    COL_OUR_NAME,
    COL_OUR_PRICE,
    PRICE_LOWER_CONTAINS,
)
from core.enums import SectionType
from core.exceptions import DataLossError
from core.models import ReconciliationReport

# مفاتيح الأقسام الخمسة التي يوزّعها _split_results (المفقود مسار منفصل).
SECTION_KEYS: tuple[str, ...] = (
    "price_raise", "price_lower", "approved", "review", "excluded",
)

# البادئات الحرفية (#PRESERVED_LOGIC app.py:473-477).
_RAISE = SectionType.PRICE_RAISE.prefix      # 🔴
_LOWER = SectionType.PRICE_LOWER.prefix      # 🟢
_APPROVED = SectionType.APPROVED.prefix      # ✅
_REVIEW = SectionType.REVIEW.prefix          # ⚠️
_MISSING = SectionType.MISSING.prefix        # 🔍
_EXCLUDED = SectionType.EXCLUDED.prefix      # ⚪


def split_results(
    df: pd.DataFrame | None, decision_col: str = COL_DECISION,
) -> dict[str, pd.DataFrame]:
    """يوزّع DataFrame على الأقسام الخمسة + ``all``. نقل نقي لـ _split_results."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        empty = pd.DataFrame()
        return {**{k: empty.copy() for k in SECTION_KEYS}, "all": empty.copy()}

    work = df.copy()
    if decision_col not in work.columns:
        work[decision_col] = ""
    work[decision_col] = work[decision_col].fillna("").astype(str).str.strip()
    dec = work[decision_col]

    # ── توزيع حصري بالبادئة (app.py:472-477) ──
    price_raise = work[dec.str.startswith(_RAISE)]
    price_lower = work[
        dec.str.startswith(_LOWER)
        | dec.str.contains(PRICE_LOWER_CONTAINS, na=False, regex=False)
    ]
    approved = work[dec.str.startswith(_APPROVED)]
    review = work[dec.str.startswith(_REVIEW) | dec.str.startswith(_MISSING)]
    excluded = work[dec.str.startswith(_EXCLUDED)]

    # ── شبكة الأمان: أي منتج غير مُوزَّع → «مستبعد» (app.py:479-489) ──
    distributed: set[int] = set()
    for section in (price_raise, price_lower, approved, review, excluded):
        distributed.update(section.index.tolist())
    orphans = work[~work.index.isin(distributed)]
    if not orphans.empty:
        excluded = pd.concat([excluded, orphans], ignore_index=False)

    return {
        "price_raise": price_raise.reset_index(drop=True),
        "price_lower": price_lower.reset_index(drop=True),
        "approved": approved.reset_index(drop=True),
        "review": review.reset_index(drop=True),
        "excluded": excluded.reset_index(drop=True),
        "all": work.reset_index(drop=True),
    }


# سبب الاستبعاد التلقائي للمنتجات التي لم يُنتج لها المحرّك صفّ تحليل
# (تستر/أجهزة/أطقم/بخور أو عطر بلا أي مطابقة سعرية في قاعدة المنافسين).
RECOVER_DECISION: str = f"{SectionType.EXCLUDED.prefix} بلا مطابقة سعرية (مُرشَّح تلقائياً)"


def _our_name_column(df: pd.DataFrame) -> str:
    """يحدّد عمود اسم المنتج في كتالوجنا (داخلي أولاً ثم استدلال). خالص."""
    if COL_OUR_NAME in df.columns:
        return COL_OUR_NAME
    for col in df.columns:
        if any(k in str(col) for k in ("اسم", "أسم", "المنتج", "name", "product")):
            return col
    return df.columns[0] if len(df.columns) else COL_OUR_NAME


def recover_uncovered_catalog(
    our_df: pd.DataFrame | None,
    sections: dict[str, pd.DataFrame],
    *,
    reason: str = RECOVER_DECISION,
) -> tuple[dict[str, pd.DataFrame], int]:
    """شبكة أمان حفظ البيانات على مستوى الكتالوج (خالصة، بلا Streamlit).

    المحرّك يُرشّح بعض منتجاتنا (تستر/أجهزة/أطقم/بلا مطابقة) **قبل** إنتاج
    صفوف التحليل، فلا تظهر في أيّ قسم — يختلّ القانون ``total_in == Σ(الأقسام)``
    ويختفي المنتج بصمت. هذه الدالة تلتقط كل اسم في الكتالوج غائب عن جميع
    الأقسام وتُعيد إدراجه في «مستبعد» بسبب واضح (محاذى لأعمدة القسم، الباقي
    NaN)، فيُغلق التوازن ويراه المستخدم بدل اختفائه.

    تُعدّل ``sections["excluded"]`` في مكانه وتُعيد ``(sections, عدد المستردّ)``.
    """
    if our_df is None or not isinstance(our_df, pd.DataFrame) or our_df.empty:
        return sections, 0
    ncol = _our_name_column(our_df)
    names = our_df[ncol].astype(str).str.strip()

    present: set[str] = set()
    for key in SECTION_KEYS:
        df = sections.get(key)
        if isinstance(df, pd.DataFrame) and COL_OUR_NAME in df.columns:
            present |= set(df[COL_OUR_NAME].astype(str).str.strip())

    uncovered_mask = (~names.isin(present)) & (names != "") & (names.str.lower() != "nan")
    uncovered = our_df[uncovered_mask].copy()
    # اسم واحد لكل منتج غير مُغطّى (المحرّك يجمّع بالاسم).
    uncovered = uncovered.loc[~names[uncovered.index].duplicated()]
    if uncovered.empty:
        return sections, 0

    exc = sections.get("excluded")
    cols = (
        list(exc.columns)
        if isinstance(exc, pd.DataFrame) and len(exc.columns)
        else [COL_OUR_NAME, COL_OUR_PRICE, COL_DECISION]
    )
    # ضمان وجود الأعمدة الأساسية حتى إذا اختلفت أعمدة القسم المستبعد
    for _col in (COL_OUR_NAME, COL_OUR_PRICE, COL_DECISION):
        if _col not in cols:
            cols.append(_col)
    rec = pd.DataFrame({c: pd.Series([pd.NA] * len(uncovered)) for c in cols})
    rec[COL_OUR_NAME] = uncovered[ncol].astype(str).str.strip().to_numpy()
    if COL_OUR_PRICE in cols and COL_OUR_PRICE in our_df.columns:
        rec[COL_OUR_PRICE] = pd.to_numeric(uncovered[COL_OUR_PRICE], errors="coerce").to_numpy()
    rec[COL_DECISION] = reason

    if isinstance(exc, pd.DataFrame) and not exc.empty:
        sections["excluded"] = pd.concat([exc, rec], ignore_index=True)
    else:
        sections["excluded"] = rec
    return sections, len(rec)


@dataclass(frozen=True)
class SplitResult:
    """نتيجة التوزيع: الأقسام الخمسة + الكل + أدوات العدّ والتدقيق."""

    sections: dict[str, pd.DataFrame]
    all_df: pd.DataFrame

    def counts(self) -> dict[str, int]:
        """عدد المنتجات في كل قسم."""
        return {k: len(self.sections[k]) for k in SECTION_KEYS}

    @property
    def total_in(self) -> int:
        return len(self.all_df)

    @property
    def total_out(self) -> int:
        return sum(self.counts().values())

    @property
    def gap(self) -> int:
        """الفجوة = الكل − المجموع (يجب أن تساوي صفراً). #PRESERVED_LOGIC app.py:533."""
        return self.total_in - self.total_out

    def report(
        self,
        duplicate_count: int = 0,
        duplicate_details: tuple[str, ...] = (),
    ) -> ReconciliationReport:
        """يبني تقرير تدقيق حفظ البيانات."""
        return ReconciliationReport.from_sections(
            self.total_in, self.counts(), duplicate_count, list(duplicate_details),
        )


class ClassificationService:
    """خدمة التصنيف: تغلّف ``split_results`` وتفرض قانون حفظ البيانات."""

    def __init__(self, decision_col: str = COL_DECISION) -> None:
        self._decision_col = decision_col

    def classify(self, df: pd.DataFrame | None, *, strict: bool = False) -> SplitResult:
        """يوزّع المنتجات. مع ``strict=True`` يرفع DataLossError عند أي فجوة."""
        raw = split_results(df, self._decision_col)
        result = SplitResult({k: raw[k] for k in SECTION_KEYS}, raw["all"])
        if strict and result.gap != 0:
            raise DataLossError(
                gap=result.gap,
                all_count=result.total_in,
                sum_buckets=result.total_out,
            )
        return result
