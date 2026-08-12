"""ui/components/review_panel.py — لوحة «إعادة توزيع المراجعة بالذكاء الاصطناعي» (المرحلة B في التطبيق).

تعرض حصيلة تصنيف AI للمراجعة (من سجلّ القرارات) + حلقة **تعلُّم مستمر**: يصحّح
المستخدم أيّ حكم خاطئ فيُدوَّن كـ ``source=user`` ويصبح مثال few-shot للتشغيلات
القادمة. زرّ اختياري لتصنيف دفعة متبقّية داخل التطبيق (محدود + تأكيد ⇒ بلا تعليق
أو إنفاق مفاجئ). كل المنطق خالص قابل للاختبار؛ ``render_*`` يستورد Streamlit كسولاً.

#PRESERVED_LOGIC: سجلّ القرارات JSONL = مصدر الحصيلة والتعلّم (ui.review_ai).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from conf.constants import COL_COMP_NAME
from ui.reconcile import _COL_MATCHED, _s
from ui.review_ai import (
    DUPLICATE,
    UNIQUE,
    UNSURE,
    _DECISIONS_FILE,
    append_jsonl,
    read_jsonl,
)

_RECON = "reconcile"
_AR_TO_CONST = {"مكرر": DUPLICATE, "فريد": UNIQUE, "غير مؤكد": UNSURE}


def _decisions_path(data_dir: Any) -> Path:
    return Path(data_dir) / _RECON / _DECISIONS_FILE


def latest_verdicts(decisions: list[Any]) -> dict[str, str]:
    """آخر حكم لكل منافس (تصحيح المستخدم يطغى على حكم AI). خالص."""
    out: dict[str, str] = {}
    for rec in decisions:
        if not isinstance(rec, dict):
            continue
        comp = _s(rec.get("comp"))
        if not comp:
            continue
        # user يكتب لاحقاً ⇒ آخر سجلّ لنفس المنافس هو الأحدث (الترتيب زمني)
        out[comp] = _s(rec.get("verdict")) or UNSURE
    return out


def progress_summary(decisions: list[Any]) -> dict[str, int]:
    """حصيلة {classified, duplicate, unique, unsure, corrections}. خالص."""
    verdicts = latest_verdicts(decisions)
    summary = {
        "classified": len(verdicts),
        "duplicate": sum(1 for v in verdicts.values() if v == DUPLICATE),
        "unique": sum(1 for v in verdicts.values() if v == UNIQUE),
        "unsure": sum(1 for v in verdicts.values() if v == UNSURE),
        "corrections": sum(
            1 for r in decisions
            if isinstance(r, dict) and r.get("source") == "user"
        ),
    }
    return summary


def unclassified_rows(review_df: Any, decisions: list[Any]) -> list[dict]:
    """صفوف مراجعة لم تُصنَّف بعد (منافسها غير مدوّن). خالص."""
    if not isinstance(review_df, pd.DataFrame) or review_df.empty \
            or COL_COMP_NAME not in review_df.columns:
        return []
    done = set(latest_verdicts(decisions))
    rows = review_df.to_dict("records")
    return [r for r in rows if _s(r.get(COL_COMP_NAME)) not in done]


def correction_record(comp: str, cand: str, verdict_ar: str) -> dict[str, str]:
    """يبني سجلّ تصحيح مستخدم (يُحوّل الحكم العربي إلى ثابت). خالص."""
    return {
        "comp": _s(comp), "cand": _s(cand),
        "verdict": _AR_TO_CONST.get(_s(verdict_ar), UNSURE), "source": "user",
    }


def _review_subset(missing_df: Any) -> pd.DataFrame:
    """صفوف «تحت المراجعة» من المفقودات (آمن)."""
    if not isinstance(missing_df, pd.DataFrame) or "مستوى_الثقة" not in missing_df.columns:
        return pd.DataFrame()
    return missing_df[missing_df["مستوى_الثقة"].astype(str) == "review"]


def render_review_ai_panel(
    missing_df: Any,
    data_dir: Any,
    *,
    ai_service: Optional[Any] = None,
) -> None:
    """يعرض حصيلة تصنيف AI للمراجعة + نموذج تصحيح (تعلّم مستمر)."""
    import streamlit as st

    review_df = _review_subset(missing_df)
    total = len(review_df)
    decisions = read_jsonl(_decisions_path(data_dir))
    summary = progress_summary(decisions)

    with st.expander("🤖 إعادة توزيع «تحت المراجعة» بالذكاء الاصطناعي + التعلّم"):
        if total == 0:
            st.info("لا صفوف تحت المراجعة.")
            return
        done = summary["classified"]
        st.progress(min(done / total, 1.0) if total else 0.0,
                    text=f"صُنّف {done:,} من {total:,} ({100*done/max(total,1):.0f}%)")
        cols = st.columns(4)
        cols[0].metric("مكرر (أُعيد توزيعه)", f"{summary['duplicate']:,}")
        cols[1].metric("فريد (مفقود فعلاً)", f"{summary['unique']:,}")
        cols[2].metric("غير مؤكد", f"{summary['unsure']:,}")
        cols[3].metric("تصحيحاتك (تعلّم)", f"{summary['corrections']:,}")
        if done == 0:
            st.caption("لم يبدأ التصنيف بعد (أو التشغيل الكامل قيد العمل في الخلفية).")

        st.divider()
        st.caption("🧠 صحّح أي حكم خاطئ ليتعلّم منه الذكاء الاصطناعي في التشغيل القادم:")
        names = review_df[COL_COMP_NAME].astype(str).tolist()
        if names:
            pick = st.selectbox("اختر منتج منافس", names[:500], key="rev_corr_pick")
            row = review_df[review_df[COL_COMP_NAME].astype(str) == pick].head(1)
            cand = _s(row.iloc[0].get(_COL_MATCHED)) if not row.empty else ""
            st.caption(f"منتجنا المرشّح: {cand or '—'}")
            choice = st.radio("الحكم الصحيح", ("مكرر", "فريد", "غير مؤكد"),
                              horizontal=True, key="rev_corr_verdict")
            if st.button("💾 دوّن التصحيح", key="rev_corr_save"):
                append_jsonl(
                    _decisions_path(data_dir),
                    [correction_record(pick, cand, choice)],
                )
                st.success("تم التدوين — سيتعلّم منه الذكاء الاصطناعي. أعد التشغيل لتطبيقه.")
