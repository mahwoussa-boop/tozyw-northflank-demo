"""ui/pages/dashboard.py — لوحة التحكم (رفع + تحليل + مؤشرات + بطاقات أقسام + إحصائيات جديدة).

غلاف رفيع: المنطق في الخدمات، والتحليل عبر رد نداء محقون (``on_analyze``).
دوال الحالة/المؤشرات/الملخّص خالصة وقابلة للاختبار بلا Streamlit.
"""
from __future__ import annotations

import html
from typing import Any, Callable, Optional

import pandas as pd

from conf.constants import COLORS, SECTION_LABELS
from ui.state_manager import AppState


def _h(value: Any) -> str:
    """ترميز HTML آمن (يمنع تسرّب رموز اسم المنتج/المتجر إلى الوسم)."""
    return html.escape(str(value), quote=True)


def conservation_status(report: Any) -> tuple[bool, str]:
    """حالة شريط حفظ البيانات (أخضر إن لا ضياع ولا تكرار)."""
    if report is None:
        return True, "ℹ️ لم يُجرَ تحليل بعد"
    if report.is_balanced:
        return True, "✅ حفظ البيانات سليم (لا ضياع ولا تكرار)"
    return False, f"❌ خلل: فجوة={report.gap} · تكرار={report.duplicate_count}"


def kpi_metrics(result: Any) -> list[tuple[str, int]]:
    """مؤشرات الأقسام من نتيجة التحليل (خالص)."""
    counts = getattr(result, "section_counts", None)
    if not counts:
        return []
    return [(SECTION_LABELS[section], count) for section, count in counts.items()]


def kpi_breakdown(result: Any) -> list[tuple[str, int, Optional[float]]]:
    """مؤشرات الأقسام مع نسبتها من **كتالوجنا** (خالص).

    «مفقود» منتجات منافسين لا نملكها — كونٌ آخر لا جزء من الكتالوج. جمعها في
    المقام كان يُصغّر كل نسبة ‎~3.3×‎: «رفع سعر» يظهر 12.7% وحقيقته 41.4% من
    الكتالوج (قياس على آخر تحليل: كتالوج 11,518 مقابل مقام 37,647).
    لذلك تُعرض بعددها بلا نسبة (``None``) بدل نسبة على قاعدة مختلطة.
    """
    counts = getattr(result, "section_counts", None)
    if not counts:
        return []
    from core.enums import SectionType

    catalog_total = sum(
        count for section, count in counts.items() if section != SectionType.MISSING
    ) or 1
    return [
        (
            SECTION_LABELS[section],
            count,
            None if section == SectionType.MISSING else count / catalog_total * 100,
        )
        for section, count in counts.items()
    ]


def top_urgent_products(
    sections: Optional[dict], n: int = 5,
) -> list[dict[str, Any]]:
    """أهم ``n`` فرصة ربح: من «🟢 سعر أقل» (سعرنا أقل من المنافس) مرتّبة تنازلياً
    بأكبر فجوة (المنافس − سعرنا) = أكبر ربح ضائع يمكن استرداده برفع السعر.

    دالة خالصة قابلة للاختبار. تُحسب الفجوة مُتجَّهةً من السعرين مباشرةً (لا تعتمد
    على إشارة عمود «الفرق»)، ثم تُقتطع أعلى ``n`` (بلا حلقة عرض ثقيلة).
    """
    from conf.constants import (
        COL_COMP_PRICE, COL_COMP_STORE, COL_OUR_NAME, COL_OUR_PRICE,
    )

    if not sections:
        return []
    df = sections.get("price_lower")
    if df is None or getattr(df, "empty", True):
        return []
    if COL_OUR_PRICE not in df.columns or COL_COMP_PRICE not in df.columns:
        return []
    tmp = df.copy()
    our = pd.to_numeric(tmp[COL_OUR_PRICE], errors="coerce").fillna(0.0)
    comp = pd.to_numeric(tmp[COL_COMP_PRICE], errors="coerce").fillna(0.0)
    tmp["_gap"] = (comp - our).clip(lower=0.0)            # ربح قابل للاسترداد فقط
    tmp = tmp[(comp > 0) & (tmp["_gap"] > 0)]             # فرص حقيقية فقط
    tmp = tmp.sort_values("_gap", ascending=False).head(max(n, 0))

    def _f(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    return [
        {
            "name": str(r.get(COL_OUR_NAME, "") or "—"),
            "our_price": _f(r.get(COL_OUR_PRICE, 0)),
            "comp_price": _f(r.get(COL_COMP_PRICE, 0)),
            "store": str(r.get(COL_COMP_STORE, "") or ""),
            "gap": _f(r.get("_gap", 0)),
        }
        for _, r in tmp.iterrows()
    ]


def top_threats(
    sections: Optional[dict], n: int = 5,
) -> list[dict[str, Any]]:
    """أهم ``n`` تهديد: من «🔴 سعر أعلى» (سعرنا أعلى من المنافس) مرتّبة تنازلياً
    بأكبر فجوة (سعرنا − المنافس) = أكبر خطر خسارة مبيعات.
    """
    from conf.constants import (
        COL_COMP_PRICE, COL_COMP_STORE, COL_OUR_NAME, COL_OUR_PRICE,
    )

    if not sections:
        return []
    df = sections.get("price_raise")
    if df is None or getattr(df, "empty", True):
        return []
    if COL_OUR_PRICE not in df.columns or COL_COMP_PRICE not in df.columns:
        return []
    tmp = df.copy()
    our = pd.to_numeric(tmp[COL_OUR_PRICE], errors="coerce").fillna(0.0)
    comp = pd.to_numeric(tmp[COL_COMP_PRICE], errors="coerce").fillna(0.0)
    tmp["_gap"] = (our - comp).clip(lower=0.0)
    tmp = tmp[(comp > 0) & (tmp["_gap"] > 0)]
    tmp = tmp.sort_values("_gap", ascending=False).head(max(n, 0))

    def _f(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    return [
        {
            "name": str(r.get(COL_OUR_NAME, "") or "—"),
            "our_price": _f(r.get(COL_OUR_PRICE, 0)),
            "comp_price": _f(r.get(COL_COMP_PRICE, 0)),
            "store": str(r.get(COL_COMP_STORE, "") or ""),
            "gap": _f(r.get("_gap", 0)),
        }
        for _, r in tmp.iterrows()
    ]


def competitor_summary(sections: Optional[dict]) -> list[dict[str, Any]]:
    """ملخّص لكل منافس: عدد المنتجات المتاحة والسعر المتوسط."""
    from conf.constants import COL_COMP_STORE, COL_COMP_PRICE

    if not sections:
        return []
    rows = []
    for sec_name in ("price_raise", "price_lower", "approved"):
        df = sections.get(sec_name)
        if df is None or getattr(df, "empty", True):
            continue
        if COL_COMP_STORE not in df.columns or COL_COMP_PRICE not in df.columns:
            continue
        prices = pd.to_numeric(df[COL_COMP_PRICE], errors="coerce")
        for store, grp in df.groupby(COL_COMP_STORE):
            prices_valid = pd.to_numeric(grp[COL_COMP_PRICE], errors="coerce").dropna()
            rows.append({
                "store": str(store),
                "count": len(grp),
                "avg_price": round(float(prices_valid.mean()), 0) if not prices_valid.empty else 0,
                "min_price": round(float(prices_valid.min()), 0) if not prices_valid.empty else 0,
                "max_price": round(float(prices_valid.max()), 0) if not prices_valid.empty else 0,
            })
    if not rows:
        return []
    # تجميع وإزالة التكرار (نفس المتجر قد يظهر في أكثر من قسم)
    summary = {}
    for r in rows:
        store = r["store"]
        if store in summary:
            summary[store]["count"] += r["count"]
            summary[store]["avg_price"] = round(
                (summary[store]["avg_price"] + r["avg_price"]) / 2, 0
            )
            summary[store]["min_price"] = min(summary[store]["min_price"], r["min_price"])
            summary[store]["max_price"] = max(summary[store]["max_price"], r["max_price"])
        else:
            summary[store] = dict(r)
    return sorted(summary.values(), key=lambda x: x["count"], reverse=True)


def price_stats_summary(sections: Optional[dict]) -> dict[str, Any]:
    """إحصائيات سعرية موجزة: متوسط الفروق، عدد المنتجات، إلخ."""
    from conf.constants import COL_COMP_PRICE, COL_OUR_PRICE, COL_DIFF

    total_products = 0
    total_gap = 0.0
    raise_count = 0
    lower_count = 0
    raise_gap = 0.0
    lower_gap = 0.0

    for sec_name in ("price_raise", "price_lower", "approved"):
        df = sections.get(sec_name) if sections else None
        if df is None or getattr(df, "empty", True):
            continue
        total_products += len(df)
        if COL_DIFF in df.columns:
            diffs = pd.to_numeric(df[COL_DIFF], errors="coerce").dropna()
            total_gap += float(diffs.abs().sum())
        if sec_name == "price_raise" and COL_OUR_PRICE in df.columns and COL_COMP_PRICE in df.columns:
            our = pd.to_numeric(df[COL_OUR_PRICE], errors="coerce").fillna(0)
            comp = pd.to_numeric(df[COL_COMP_PRICE], errors="coerce").fillna(0)
            gap = (our - comp).clip(lower=0)
            raise_count = len(df[gap > 0])
            raise_gap = float(gap.sum())
        if sec_name == "price_lower" and COL_OUR_PRICE in df.columns and COL_COMP_PRICE in df.columns:
            our = pd.to_numeric(df[COL_OUR_PRICE], errors="coerce").fillna(0)
            comp = pd.to_numeric(df[COL_COMP_PRICE], errors="coerce").fillna(0)
            gap = (comp - our).clip(lower=0)
            lower_count = len(df[gap > 0])
            lower_gap = float(gap.sum())

    return {
        "total_products": total_products,
        "raise_count": raise_count,
        "raise_gap": raise_gap,
        "lower_count": lower_count,
        "lower_gap": lower_gap,
        "avg_gap": round(total_gap / max(total_products, 1), 0),
    }


def last_analysis_summary(result: Any) -> Optional[tuple[str, int]]:
    """ملخّص آخر تحليل (التاريخ، الإجمالي) أو None إن لم يُجرَ تحليل. خالص."""
    if result is None:
        return None
    created = getattr(result, "created_at", None)
    counts = getattr(result, "section_counts", None) or {}
    total = int(getattr(result, "total", 0) or sum(counts.values()))
    date_str = created.strftime("%Y-%m-%d %H:%M") if created is not None else "—"
    return date_str, total


def quality_report_data(df: Optional[pd.DataFrame]) -> Optional[dict]:
    """يُجمِّع بيانات تقرير جودة البيانات من DataFrame (دالة خالصة بلا Streamlit).

    يُنشئ ``DataQualityScorer`` محلياً (بلا Container) ويُعيد قاموساً
    يصلح للعرض أو الاختبار. يعيد ``None`` إذا كان الإدخال فارغاً.

    المعاملات:
        df: جدول المنتجات (قد يكون None).

    المخرجات:
        قاموس يحتوي: overall, grade, completeness, consistency,
        price_validity, freshness, total_rows, issues (كقائمة قواميس).
    """
    if df is None or df.empty:
        return None

    from services.monitoring.data_quality import DataQualityScorer

    scorer = DataQualityScorer()
    result = scorer.score(df)

    return {
        "overall": result.overall,
        "grade": result.grade,
        "completeness": result.completeness,
        "consistency": result.consistency,
        "price_validity": result.price_validity,
        "freshness": result.freshness,
        "total_rows": result.total_rows,
        "issues": [
            {
                "issue_id": iss.issue_id,
                "severity": iss.severity,
                "category": iss.category,
                "message": iss.message,
                "affected_rows": iss.affected_rows,
                "sample_values": iss.sample_values,
            }
            for iss in result.issues
        ],
    }


def _catalog_fingerprint(df: Optional[pd.DataFrame]) -> tuple:
    """بصمة رخيصة للكاش: (عدد الصفوف، أسماء الأعمدة). لا تهزّ الـDataFrame كله.

    كافية لتمييز أي كتالوج مختلف عملياً (تحليل جديد ⇒ صفوف/أعمدة مختلفة)،
    وتتجنّب كلفة هاش ملايين الخلايا في ``st.cache_data``.
    """
    if df is None:
        return (0, ())
    return (int(len(df)), tuple(str(c) for c in df.columns))


_quality_cache: Any = None  # يُهيّأ كسولاً — يُبقي streamlit خارج استيراد الوحدة


def _get_quality_cache() -> Any:
    """نسخة ``quality_report_data`` المُخزَّنة مؤقتاً (تُبنى مرّة، هويّة ثابتة عبر التشغيلات).

    المفتاح = البصمة الرخيصة فقط؛ الـ``_df`` (بسابقة ``_``) لا يُهاش — يُمرَّر للحساب
    عند الحاجة. التخزين لا يغيّر الناتج — يعيد استخدام حساب نفس البصمة فقط.
    """
    global _quality_cache
    if _quality_cache is None:
        import streamlit as st

        @st.cache_data(show_spinner=False)
        def _compute(fingerprint: tuple, _df: Optional[pd.DataFrame]) -> Optional[dict]:
            return quality_report_data(_df)

        _quality_cache = _compute
    return _quality_cache


# ── ألوان التصنيفات ──────────────────────────────────────────────
_GRADE_COLORS: dict[str, str] = {
    "A": "#10B981",
    "B": "#3B82F6",
    "C": "#F59E0B",
    "D": "#F97316",
    "F": "#EF4444",
}

_SEVERITY_ICONS: dict[str, str] = {
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
}

_DIMENSION_LABELS: dict[str, str] = {
    "completeness": "الاكتمال",
    "consistency": "التناسق",
    "price_validity": "صحة الأسعار",
    "freshness": "الحداثة",
}


def _render_quality_report(state: AppState) -> None:
    """يعرض بطاقة تقرير جودة البيانات بتصميم زجاجي متسق مع هوية لوحة التحكم.

    يستدعي ``quality_report_data`` ثم يبني HTML بتنسيق RTL
    يتضمن شارة التصنيف وأشرطة الأبعاد الأربعة وقائمة المشاكل.
    """
    import streamlit as st

    catalog = state.our_catalog
    report = _get_quality_cache()(_catalog_fingerprint(catalog), catalog)
    if report is None:
        return

    grade = report["grade"]
    overall = report["overall"]
    grade_color = _GRADE_COLORS.get(grade, "#818CF8")

    # ── أشرطة الأبعاد الأربعة ────────────────────────────────
    bars_html = ""
    for key, label in _DIMENSION_LABELS.items():
        value = report.get(key, 0.0)
        # لون الشريط يتدرج مع القيمة
        if value >= 75:
            bar_color = "#10B981"
        elif value >= 50:
            bar_color = "#F59E0B"
        else:
            bar_color = "#EF4444"
        bars_html += (
            f'<div style="margin-bottom:8px">'
            f'<div style="display:flex;justify-content:space-between;'
            f'font-size:.75rem;color:#94A3B8;margin-bottom:3px">'
            f'<span>{label}</span><span>{value:.0f}%</span></div>'
            f'<div style="background:#1F2937;border-radius:3px;height:6px;overflow:hidden">'
            f'<div style="width:{value}%;height:100%;background:{bar_color};'
            f'border-radius:3px;transition:width .4s ease"></div>'
            f'</div></div>'
        )

    # ── البطاقة الرئيسية ─────────────────────────────────────
    card_html = (
        f'<div style="background:linear-gradient(180deg,rgba(22,22,26,.95),rgba(15,15,18,.95));'
        f'border:1px solid #1F2937;border-radius:14px;padding:20px 24px;margin:10px 0 14px;'
        f'direction:rtl;font-family:Tajawal,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,.2)">'
        # ── الصف العلوي: شارة التصنيف + العنوان ──
        f'<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">'
        f'<div style="min-width:64px;height:64px;border-radius:14px;'
        f'background:{grade_color}18;border:2px solid {grade_color};'
        f'display:flex;flex-direction:column;align-items:center;justify-content:center">'
        f'<span style="font-size:1.7rem;font-weight:900;color:{grade_color};'
        f'line-height:1">{grade}</span>'
        f'<span style="font-size:.6rem;color:{grade_color};opacity:.8">{overall:.0f}/100</span>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:1rem;font-weight:700;color:#E2E8F0;margin-bottom:2px">'
        f'📊 تقرير جودة البيانات</div>'
        f'<div style="font-size:.75rem;color:#94A3B8">'
        f'{report["total_rows"]:,} صف · {len(report["issues"])} ملاحظة</div>'
        f'</div></div>'
        # ── أشرطة الأبعاد ──
        f'{bars_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    # ── قائمة المشاكل (قابلة للطي) ─────────────────────────
    issues = report.get("issues", [])
    if issues:
        with st.expander(f"🔍 تفاصيل الملاحظات ({len(issues)})", expanded=False):
            for iss in issues:
                icon = _SEVERITY_ICONS.get(iss["severity"], "ℹ️")
                sev_color = {
                    "error": "#EF4444",
                    "warning": "#F59E0B",
                    "info": "#94A3B8",
                }.get(iss["severity"], "#94A3B8")
                sample_text = ""
                if iss.get("sample_values"):
                    sample_text = (
                        f'<div style="font-size:.68rem;color:#64748B;'
                        f'margin-top:2px">'
                        f'أمثلة: {", ".join(str(v) for v in iss["sample_values"][:3])}'
                        f'</div>'
                    )
                issue_html = (
                    f'<div style="background:rgba(22,22,26,.7);border:1px solid #1F2937;'
                    f'border-right:3px solid {sev_color};border-radius:8px;'
                    f'padding:8px 12px;margin-bottom:6px;direction:rtl;'
                    f'font-family:Tajawal,sans-serif">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center">'
                    f'<span style="font-size:.8rem;color:#E2E8F0">'
                    f'{icon} {iss["message"]}</span>'
                    f'<span style="font-size:.68rem;color:{sev_color};'
                    f'font-weight:600">{iss["affected_rows"]:,} صف</span>'
                    f'</div>'
                    f'{sample_text}'
                    f'</div>'
                )
                st.markdown(issue_html, unsafe_allow_html=True)


def _render_upload(state: AppState, on_analyze: Optional[Callable[..., Any]]) -> None:
    """منطقة الرفع + زرّ تحليل يعمل في خيط خلفي محصّن ضد الانقطاع.

    الجذر (حادثة 2026-07-12): التحليل المتزامن داخل دورة العرض كان يضيع عرضُه
    بأي ضغطة/تحديث صفحة أثناء الدقائق الطويلة. الآن يشغّله ``analysis_runner``
    في خيط خادم مستقل عن الجلسة ويحفظ نتائجه بنفسه. تدهور رشيق: أي تعذّر في
    مشغّل الخلفية يرتد للمسار المتزامن القديم (``on_analyze``) بلا تغيير سلوك.
    """
    import streamlit as st

    try:
        from services import analysis_runner as runner
    except Exception:  # مشغّل الخلفية غير متاح ⇒ السلوك القديم حرفياً
        runner = None  # type: ignore[assignment]

    prog = runner.read_progress() if runner else None
    running = bool(runner and runner.is_running(prog))

    uploaded = st.file_uploader("📤 ارفع كتالوجنا (CSV/Excel)", type=["csv", "xlsx"])
    can_run = uploaded is not None and (runner is not None or on_analyze is not None)
    if st.button("🚀 ابدأ التحليل", disabled=not can_run or running or state.is_analysis_running,
                 type="primary", use_container_width=True):
        if runner is not None:
            try:
                ok, msg = runner.start_background_analysis(
                    uploaded.getvalue(), uploaded.name,
                )
            except Exception as exc:  # عطل غير متوقع في المشغّل ⇒ ارتداد رشيق
                ok, msg = False, f"__fallback__ {exc}"
            if ok:
                st.rerun()  # يُظهر شريط التقدم الحي فوراً
            elif msg.startswith("__fallback__") and on_analyze is not None:
                _run_sync_analysis(on_analyze, uploaded)
            else:
                st.warning(msg)
        elif on_analyze is not None:
            _run_sync_analysis(on_analyze, uploaded)

    if runner is not None:
        if running:
            _render_analysis_progress(runner)
        elif prog and prog.get("done"):
            _render_analysis_outcome(state, prog)


def _run_sync_analysis(on_analyze: Callable[..., Any], uploaded: Any) -> None:
    """المسار المتزامن القديم كما هو — يُستخدم فقط عند تعذّر مشغّل الخلفية."""
    import streamlit as st

    with st.status("⏳ جاري التحليل الكامل…", expanded=True) as status:
        bar = st.progress(0, text="بدء التحليل…")
        bar.progress(30, text="كشف المفقودات ومطابقة المنافسين…")
        on_analyze(uploaded)
        bar.progress(100, text="اكتمل")
        status.update(label="✅ اكتمل التحليل", state="complete")


def _render_analysis_progress(runner: Any) -> None:
    """لوحة تقدم حية (fragment كل 5 ثوانٍ) لتحليل الخلفية — تنجو من تحديث الصفحة."""
    import streamlit as st

    @st.fragment(run_every="5s")
    def _panel() -> None:
        p = runner.read_progress() or {}
        if p.get("done") or not runner.is_running(p):
            st.rerun(scope="app")  # اكتمل/توقف ⇒ إعادة عرض كاملة للحالة النهائية
            return
        elapsed, estimate = runner.elapsed_and_estimate(p)
        ratio = min(0.95, elapsed / estimate)  # لا 100% قبل الاكتمال الفعلي
        label = runner.PHASE_LABELS.get(str(p.get("phase") or ""), str(p.get("phase") or ""))
        st.info(
            "🛡️ التحليل يعمل في الخلفية — آمن ضد تحديث الصفحة وإغلاق المتصفح.\n\n"
            f"المرحلة الحالية: **{label}** · مضى **{int(elapsed // 60)} د** "
            f"من ~{int(estimate // 60)} د متوقعة (تقدير من آخر تشغيل)"
        )
        st.progress(ratio, text=f"~{ratio * 100:.0f}٪ (تقدير زمني)")

    _panel()


def _render_analysis_outcome(state: AppState, prog: dict) -> None:
    """نتيجة آخر تحليل خلفي (خلال 24 ساعة): خطأ واضح أو زر تحميل النتائج."""
    import time as _time

    import streamlit as st

    if _time.time() - float(prog.get("finished_at") or 0) > 86_400:
        return  # نتيجة قديمة — لا ضجيج
    run_id = str(prog.get("run_id") or "")
    if prog.get("error"):
        if st.session_state.get("_bg_err_ack") != run_id:
            st.error(f"❌ تعذّر التحليل الخلفي: {prog['error']}")
            if st.button("حسناً — إخفاء", key="bg_err_ack_btn"):
                st.session_state["_bg_err_ack"] = run_id
                st.rerun()
        return
    if st.session_state.get("_loaded_analysis_run") == run_id:
        return  # نتائج هذا التشغيل معروضة في هذه الجلسة أصلاً
    summary = prog.get("summary") or {}
    dur_min = int(float(prog.get("duration_sec") or 0) // 60)
    # لافتة الاكتمال كانت تُعلن سلامة الحفظ وتصمت عن خلله، فتتطابق اللافتة
    # الخضراء في الحالتين. الصمت عن الخلل أسوأ من عدم الفحص: يوحي بالسلامة.
    salvaged = int(summary.get("deterministic_salvaged", 0) or 0)
    salvaged_txt = f" · 🔎 {salvaged:,} مُنقَذ للمراجعة" if salvaged else ""
    balanced = summary.get("balanced")
    balanced_txt = " · حفظ البيانات سليم ✅" if balanced else ""
    st.success(f"✅ اكتمل تحليل في الخلفية ({dur_min} دقيقة){balanced_txt}{salvaged_txt}")
    if balanced is False:
        st.error(
            f"❌ خلل في حفظ البيانات: فجوة={summary.get('gap')} · "
            f"تكرار={summary.get('duplicates')} — راجع الأقسام قبل أي إرسال."
        )
    for _key, _msg in (
        ("salvage_error", "تعذّر إنقاذ المستبعدات"),
        ("closed_loop_error", "تعذّر التحقّق من عدم تسرّب منتجات المنافسين"),
    ):
        if summary.get(_key):
            st.warning(f"⚠️ {_msg}: {summary[_key]}")
    # ملاحظة مقصودة: dropped_none/dropped_flush_error/skipped_empty **لا تُسقط**
    # صفوفاً رغم أسمائها — المحرّك يُلحق كلاً منها صفّاً مستبعداً مرئياً يغلق
    # التوازن (engine.py:2903 و2929 و2987). لذلك لا تحذير عليها: عددها مشمول
    # في «⚪ مستبعد» أصلاً، وإظهارها كخسارة إنذار كاذب. تُمرَّر في الملخّص
    # للتشخيص فقط.
    _render_phase_timings(st, prog)
    if st.button("📥 عرض نتائج التحليل الجديد", type="primary",
                 use_container_width=True, key="bg_load_results"):
        state.sections = {}
        state.missing_df = None
        state.our_catalog = None
        state.analysis_results = None
        if state.restore_results():
            st.session_state["_loaded_analysis_run"] = run_id
            st.rerun()
        else:
            st.warning("تعذّر تحميل اللقطة الجديدة — أعد فتح التطبيق لعرضها.")


def phase_timing_rows(prog: dict) -> list[tuple[str, float, float]]:
    """(اسم الطور بالعربية، ثوانيه، نسبته من المجموع) تنازلياً. خالصة.

    مفصولة عن العرض كي تُختبَر بلا Streamlit — والنسبة تُحسب من **مجموع
    الأطوار** لا من ``duration_sec``: لو اختلفا (طور لم يُختم لانقطاع) لظهرت
    نسبٌ لا تجمع 100% فتوحي بزمن ضائع لا وجود له.
    """
    from services.analysis_runner import PHASE_LABELS

    timings = prog.get("phase_timings") or {}
    if not isinstance(timings, dict) or not timings:
        return []
    clean = {k: float(v) for k, v in timings.items()
             if isinstance(v, (int, float)) and float(v) >= 0}
    total = sum(clean.values())
    if total <= 0:
        return []
    labels = {**PHASE_LABELS, "boot": "تهيئة (استيراد + حاوية)"}
    return sorted(
        ((labels.get(k, k), v, 100.0 * v / total) for k, v in clean.items()),
        key=lambda r: r[1], reverse=True,
    )


def _render_phase_timings(st, prog: dict) -> None:
    """يعرض توزيع زمن التحليل على أطواره — مطوياً كي لا يزاحم اللافتة.

    لماذا يُعرض أصلاً: قياسٌ يُكتب ولا يُقرأ يصير حقلاً ميتاً (سابقة
    ``audit_stats``). وهذا الجدول هو ما يحسم أين يذهب زمن تحليلك فعلاً —
    بدلاً من الاستقراء من عيّنات.
    """
    rows = phase_timing_rows(prog)
    if not rows:
        return
    with st.expander("⏱️ أين ذهب زمن التحليل؟", expanded=False):
        for label, sec, pct in rows:
            mins = sec / 60.0
            shown = f"{mins:,.1f} دقيقة" if sec >= 60 else f"{sec:,.1f} ثانية"
            st.write(f"**{label}** — {shown} ({pct:.0f}%)")
            st.progress(min(1.0, pct / 100.0))


def _render_summary(state: AppState) -> None:
    """ملخّص آخر تحليل (شريط لاصق زجاجي مدمج)."""
    import streamlit as st

    from ui.components.page_header import section_accent

    summary = last_analysis_summary(state.analysis_results)
    if summary is None:
        return
    date_str, total = summary
    catalog = state.our_catalog
    catalog_n = len(catalog) if catalog is not None else 0
    accent = section_accent("dashboard")
    parts = (
        f'<span class="st-i">📅 آخر تحليل <b>{date_str}</b></span>'
        f'<span class="st-i">📦 إجمالي <b>{total:,}</b> منتج</span>'
    )
    if catalog_n and catalog_n != total:
        parts += f'<span class="st-i">🗂️ كتالوج <b>{catalog_n:,}</b></span>'
    # مدّة التحليل (إن سُجّلت) — دقائق وثوانٍ بصيغة مفهومة.
    dur = float(getattr(state.analysis_results, "duration_sec", 0) or 0)
    if dur > 0:
        dur_txt = f"{dur/60:.1f} دقيقة" if dur >= 60 else f"{dur:.0f} ثانية"
        parts += f'<span class="st-i">⏱️ استغرق <b>{dur_txt}</b></span>'
    st.markdown(
        f'<div class="mhw-ctrl" style="--ac:{accent}">'
        f'<div class="st-row">{parts}</div></div>',
        unsafe_allow_html=True,
    )
    st.caption("💾 النتائج تُحفظ تلقائياً وتظهر عند إعادة فتح التطبيق — لتحديثها ارفع كتالوجاً جديداً أو شغّل كشطاً ثم أعد التحليل.")


def _render_banner(state: AppState) -> None:
    """شريط حفظ البيانات (أخضر/أحمر)."""
    import streamlit as st

    report = getattr(state.analysis_results, "reconciliation", None)
    ok, text = conservation_status(report)
    if report is None:  # لا تحليل بعد — رسالة محايدة (أزرق) لا نجاح (أخضر)
        st.info(text)
    else:
        (st.success if ok else st.error)(text)


def _render_quick_stats(state: AppState) -> None:
    """إحصائيات سريعة: المنتجات المُقارنة سعرياً (أعلى+أقل+موافق)، لا كامل الكتالوج."""
    import streamlit as st

    stats = price_stats_summary(state.sections)
    if stats["total_products"] == 0:
        return

    accent = "#818CF8"
    cards = ""
    items = [
        ("📦 منتجات تمت مقارنتها", stats["total_products"], "#818CF8"),
        ("🔴 منتجات بسعر أعلى", stats["raise_count"], "#EF4444"),
        ("🟢 منتجات بسعر أقل", stats["lower_count"], "#10B981"),
        ("💰 متوسط الفرق", f"{stats['avg_gap']:,.0f} ر.س", "#F59E0B"),
    ]
    for label, value, color in items:
        val_str = f"{value:,}" if isinstance(value, int) else str(value)
        cards += (
            f'<div style="flex:1;min-width:130px;background:linear-gradient(180deg,{color}08,rgba(22,22,26,.9));'
            f'border:1px solid #1F2937;border-top:3px solid {color};'
            f'border-radius:12px;padding:12px 14px;text-align:center;'
            f'transition:transform .2s,box-shadow .2s">'
            f'<div style="font-size:.74rem;color:#94A3B8;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:1.6rem;font-weight:900;color:#E2E8F0;line-height:1.1">'
            f'{val_str}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;direction:rtl;'
        f'font-family:Tajawal,sans-serif;margin:6px 0 14px">{cards}</div>',
        unsafe_allow_html=True,
    )


def _render_kpis(state: AppState) -> None:
    """بطاقات مؤشرات الأقسام الملوّنة (HTML مدمج متناسق مع هوية التطبيق)."""
    import streamlit as st

    from core.enums import SectionType

    breakdown = kpi_breakdown(state.analysis_results)
    if not breakdown:
        return
    st.subheader("📊 توزيع الأقسام")
    # بناء بطاقات مصغرة ملوّنة لكل قسم
    _sec_colors = {
        SectionType.PRICE_RAISE: COLORS["raise"],
        SectionType.PRICE_LOWER: COLORS["lower"],
        SectionType.APPROVED: COLORS["approved"],
        SectionType.MISSING: COLORS["missing"],
        SectionType.REVIEW: COLORS["review"],
        SectionType.EXCLUDED: COLORS["excluded"],
    }
    cards_html = ""
    for label, count, pct in breakdown:
        # استخراج اللون من التسمية (الأيقونة الأولى → القسم)
        sec_key = next(
            (s for s, lbl in SECTION_LABELS.items() if lbl == label),
            None,
        )
        color = _sec_colors.get(sec_key, "#818CF8") if sec_key else "#818CF8"
        # تدرج لوني خفيف للبطاقة
        bg_color = f"{color}08"
        cards_html += (
            f'<div style="flex:1;min-width:130px;background:linear-gradient(180deg,{bg_color},rgba(22,22,26,.9));'
            f'border:1px solid #1F2937;border-top:3px solid {color};'
            f'border-radius:12px;padding:12px 14px;text-align:center;'
            f'transition:transform .2s,box-shadow .2s" class="mhw-kpi-card">'
            f'<div style="font-size:.74rem;color:#94A3B8;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:1.8rem;font-weight:900;color:#E2E8F0;line-height:1.1">'
            f'{count:,}</div>'
            f'<div style="font-size:.7rem;color:{color};font-weight:700;margin-top:4px">'
            f'{f"{pct:.1f}% من الكتالوج" if pct is not None else "خارج كتالوجنا"}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;direction:rtl;'
        f'font-family:Tajawal,sans-serif;margin:6px 0 12px">{cards_html}</div>',
        unsafe_allow_html=True,
    )


def _render_urgent(state: AppState) -> None:
    """أهم 5 منتجات تحتاج إجراءً عاجلاً (أكبر فرق سعري في «🟢 سعر أقل»)."""
    import streamlit as st

    sections = state.sections if isinstance(state.sections, dict) else {}
    rows = top_urgent_products(sections, n=5)
    if not rows:
        return
    st.subheader("🚀 أكبر فرص الربح — منتجاتك أرخص من المنافس")
    cards = ""
    for r in rows:
        gap = r["gap"]
        cards += (
            f'<div style="display:flex;align-items:center;gap:14px;'
            f'background:linear-gradient(180deg,rgba(16,185,129,.06),rgba(22,22,26,.9));'
            f'border:1px solid #1F2937;border-right:3px solid #10B981;border-radius:12px;'
            f'padding:10px 14px;margin:8px 0;direction:rtl;'
            f'transition:transform .15s,box-shadow .15s" class="mhw-urgent-card">'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:1rem;font-weight:700;color:#E2E8F0;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
            f'{_h(r["name"])}</div>'
            f'<div style="font-size:.76rem;color:#94A3B8;margin-top:2px">'
            f'سعرنا {r["our_price"]:,.0f} · المنافس {r["comp_price"]:,.0f}'
            f'{" · 🏬 " + _h(r["store"]) if r["store"] else ""}</div>'
            f'</div>'
            f'<div style="text-align:center;min-width:110px;flex-shrink:0">'
            f'<div style="font-size:1.5rem;font-weight:900;color:#10B981;'
            f'line-height:1">+{gap:,.0f}<small style="font-size:.58rem;'
            f'color:#94A3B8"> ر.س فرصة رفع</small></div></div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="font-family:Tajawal,sans-serif;margin:4px 0 14px">{cards}</div>',
        unsafe_allow_html=True,
    )


_stockout_cache: Any = None  # كاش كسول (هويّة ثابتة عبر التشغيلات — نمط _get_quality_cache)


def _get_stockout_cache() -> Any:
    """نسخة ``urgent_stockouts`` مُخزّنة (ttl=15د) — لا مسح 103k حدث كل تفاعل."""
    global _stockout_cache
    if _stockout_cache is None:
        import streamlit as st

        @st.cache_data(ttl=900, show_spinner=False)
        def _fetch() -> list:
            from services.alert_service import urgent_stockouts
            return urgent_stockouts()

        _stockout_cache = _fetch
    return _stockout_cache


def _render_urgent_stockouts(state: AppState) -> None:
    """🔥 نفد عند المنافس ومتوفر عندك — أعلى إشارة بيع لحظية (حالة فارغة صامتة)."""
    import streamlit as st

    try:
        rows = _get_stockout_cache()()
    except Exception:
        return
    if not rows:
        return
    st.subheader("🔥 نفد عند المنافس — ومتوفر عندك")
    st.caption(
        f"منتجات تبيعها نفد مخزونها عند منافسين خلال آخر ٧ أيام "
        f"({len(rows)} منتجاً) — أولوية عرض/سعر."
    )
    cards = ""
    for r in rows[:5]:
        stores = "، ".join(r["stores"][:3])
        more = f" +{r['n_stores'] - 3}" if r["n_stores"] > 3 else ""
        day = str(r["last_at"])[:10]
        cards += (
            f'<div style="display:flex;align-items:center;gap:14px;'
            f'background:linear-gradient(180deg,rgba(245,158,11,.07),rgba(22,22,26,.9));'
            f'border:1px solid #1F2937;border-right:3px solid #F59E0B;border-radius:12px;'
            f'padding:10px 14px;margin:8px 0;direction:rtl">'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:1rem;font-weight:700;color:#E2E8F0;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
            f'{_h(r["name"])}</div>'
            f'<div style="font-size:.76rem;color:#94A3B8;margin-top:2px">'
            f'🏬 نفد عند: {_h(stores)}{more} · آخر رصد {_h(day)}</div>'
            f'</div>'
            f'<div style="text-align:center;min-width:90px;flex-shrink:0">'
            f'<div style="font-size:1.35rem;font-weight:900;color:#F59E0B;line-height:1">'
            f'{r["n_stores"]}<small style="font-size:.58rem;color:#94A3B8"> متجر نافد</small>'
            f'</div></div></div>'
        )
    st.markdown(
        f'<div style="font-family:Tajawal,sans-serif;margin:4px 0 14px">{cards}</div>',
        unsafe_allow_html=True,
    )


_digest_cache: Any = None  # كاش كسول (نفس نمط _get_stockout_cache)


def _get_digest_cache() -> Any:
    """نسخة ``daily_digest`` مُخزّنة (ttl=15د) — لا مسح للأحداث كل تفاعل."""
    global _digest_cache
    if _digest_cache is None:
        import streamlit as st

        @st.cache_data(ttl=900, show_spinner=False)
        def _fetch() -> dict:
            from services.alert_service import daily_digest
            return daily_digest()

        _digest_cache = _fetch
    return _digest_cache


def _render_market_digest(state: AppState) -> None:
    """📰 ملخّص السوق اليومي — غير العاجل مجمّعاً (Expander مطويّ: تجميع لا إزعاج)."""
    import streamlit as st

    try:
        digest = _get_digest_cache()() or {}
    except Exception:
        return
    counts, movers = digest.get("counts") or {}, digest.get("movers") or []
    if not counts and not movers:
        return  # لا أحداث خلال ٢٤ ساعة ⇒ صمت (لا قسم فارغاً)
    parts = [
        f"{icon} {int(counts[key]):,} {label}"
        for key, label, icon in (
            ("price_down", "خفض سعر", "🔻"), ("price_up", "رفع سعر", "🔺"),
            ("back_in_stock", "عاد للتوفر", "📦"), ("stock_out", "نفد", "⛔"),
        ) if int(counts.get(key, 0) or 0) > 0
    ]
    with st.expander("📰 ملخّص السوق — آخر ٢٤ ساعة", expanded=False):
        if parts:
            st.caption(" · ".join(parts) + " — عند كل المنافسين")
        if not movers:
            return
        st.caption("أكبر تحرّكات الأسعار على منتجات **تبيعها**:")
        cards = ""
        for m in movers[:5]:
            down = m["new"] < m["old"]  # منافس خفض ⇒ تهديد؛ رفع ⇒ فرصة رفع
            color = "#EF4444" if down else "#10B981"
            verdict = "خفض المنافس — راقب سعرك" if down else "رفع المنافس — فرصة رفع"
            cards += (
                f'<div style="display:flex;align-items:center;gap:14px;'
                f'background:rgba(22,22,26,.9);border:1px solid #1F2937;'
                f'border-right:3px solid {color};border-radius:12px;'
                f'padding:10px 14px;margin:8px 0;direction:rtl">'
                f'<div style="flex:1;min-width:0">'
                f'<div style="font-size:.95rem;font-weight:700;color:#E2E8F0;'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
                f'{_h(m["name"])}</div>'
                f'<div style="font-size:.76rem;color:#94A3B8;margin-top:2px">'
                f'🏬 {_h(m["competitor"])} · {m["old"]:,.0f} ← {m["new"]:,.0f} ر.س'
                f' · {_h(verdict)}</div></div>'
                f'<div style="text-align:center;min-width:80px;flex-shrink:0">'
                f'<div style="font-size:1.25rem;font-weight:900;color:{color};'
                f'line-height:1">{m["pct"]:+.0f}%</div></div></div>'
            )
        st.markdown(
            f'<div style="font-family:Tajawal,sans-serif;margin:2px 0 8px">{cards}</div>',
            unsafe_allow_html=True,
        )


def _render_threats(state: AppState) -> None:
    """أهم 5 تهديدات (منتجات بسعر أعلى من المنافس)."""
    import streamlit as st

    sections = state.sections if isinstance(state.sections, dict) else {}
    rows = top_threats(sections, n=5)
    if not rows:
        return
    st.subheader("⚠️ أكبر التهديدات — منتجاتك أغلى من المنافس")
    cards = ""
    for r in rows:
        gap = r["gap"]
        cards += (
            f'<div style="display:flex;align-items:center;gap:14px;'
            f'background:linear-gradient(180deg,rgba(239,68,68,.06),rgba(22,22,26,.9));'
            f'border:1px solid #1F2937;border-right:3px solid #EF4444;border-radius:12px;'
            f'padding:10px 14px;margin:8px 0;direction:rtl;'
            f'transition:transform .15s,box-shadow .15s" class="mhw-threat-card">'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:1rem;font-weight:700;color:#E2E8F0;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
            f'{_h(r["name"])}</div>'
            f'<div style="font-size:.76rem;color:#94A3B8;margin-top:2px">'
            f'سعرنا {r["our_price"]:,.0f} · المنافس {r["comp_price"]:,.0f}'
            f'{" · 🏬 " + _h(r["store"]) if r["store"] else ""}</div>'
            f'</div>'
            f'<div style="text-align:center;min-width:110px;flex-shrink:0">'
            f'<div style="font-size:1.5rem;font-weight:900;color:#EF4444;'
            f'line-height:1">−{gap:,.0f}<small style="font-size:.58rem;'
            f'color:#94A3B8"> ر.س فرق</small></div></div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="font-family:Tajawal,sans-serif;margin:4px 0 14px">{cards}</div>',
        unsafe_allow_html=True,
    )


def _render_competitors(state: AppState) -> None:
    """ملخّص المنافسين: عدد المنتجات لكل متجر."""
    import streamlit as st

    sections = state.sections if isinstance(state.sections, dict) else {}
    comps = competitor_summary(sections)
    if not comps:
        return

    st.subheader("🏪 نظرة على المنافسين")
    cards = ""
    for c in comps[:6]:
        cards += (
            f'<div style="flex:1;min-width:160px;background:linear-gradient(180deg,rgba(30,41,59,.9),rgba(15,23,42,.95));'
            f'border:1px solid #334155;border-radius:12px;padding:12px 14px;'
            f'direction:rtl;font-family:Tajawal,sans-serif;'
            f'transition:transform .15s,box-shadow .15s">'
            f'<div style="font-size:.9rem;font-weight:700;color:#E2E8F0;margin-bottom:6px">'
            f'🏪 {_h(c["store"])}</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="font-size:.72rem;color:#94A3B8">📦 {c["count"]:,} منتج</span>'
            f'<span style="font-size:.72rem;color:#818CF8">Ø {c["avg_price"]:,.0f} ر.س</span>'
            f'</div>'
            f'<div style="font-size:.68rem;color:#64748B;margin-top:4px">'
            f'{c["min_price"]:,.0f} — {c["max_price"]:,.0f} ر.س</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;direction:rtl;'
        f'margin:6px 0 14px">{cards}</div>',
        unsafe_allow_html=True,
    )


def _render_recent_activity(state: AppState) -> None:
    """آخر 5 إجراءات من سجلّ المعالجة."""
    import streamlit as st

    entries = state.processed_log[:5]
    if not entries:
        return

    st.subheader("🕐 آخر النشاطات")
    rows = ""
    _kind_color = {"price": "#10B981", "missing": "#0EA5E9", "hidden": "#9CA3AF"}
    _kind_icon = {"price": "💰", "missing": "⚡", "hidden": "🚫"}
    for e in entries:
        kind = e.get("kind", "")
        color = _kind_color.get(kind, "#818CF8")
        icon = _kind_icon.get(kind, "✅")
        name = _h(e.get("name") or e.get("key") or "—")
        action = _h(e.get("action", ""))
        ts = _h(e.get("ts", ""))
        rows += (
            f'<div style="display:flex;align-items:center;gap:10px;'
            f'background:rgba(22,22,26,.7);border:1px solid #1F2937;'
            f'border-right:3px solid {color};border-radius:10px;'
            f'padding:8px 12px;margin:6px 0;direction:rtl;'
            f'font-family:Tajawal,sans-serif">'
            f'<span style="font-size:1.2rem">{icon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:.88rem;font-weight:700;color:#E2E8F0">'
            f'{name}</div>'
            f'<div style="font-size:.72rem;color:#94A3B8">{action}</div>'
            f'</div>'
            f'<span style="font-size:.68rem;color:#64748B;white-space:nowrap">{ts}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="font-family:Tajawal,sans-serif;margin:4px 0 14px">{rows}</div>',
        unsafe_allow_html=True,
    )


def _render_quick_actions(state: AppState) -> None:
    """أزرار اختصار سريعة للأقسام الرئيسية."""
    import streamlit as st

    sections = state.sections if isinstance(state.sections, dict) else {}
    has_data = bool(sections)

    if not has_data and state.analysis_results is None:
        return

    def _nav_to(page: str) -> None:
        # الإسناد عبر on_click (قبل إعادة التشغيل) — يتجنّب StreamlitAPIException
        st.session_state["nav_page"] = page

    st.subheader("⚡ إجراءات سريعة")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.button("🔴 سعر أعلى", use_container_width=True, key="qa_raise",
                  on_click=_nav_to, args=("🔴 سعر أعلى",))
    with c2:
        st.button("🟢 سعر أقل", use_container_width=True, key="qa_lower",
                  on_click=_nav_to, args=("🟢 سعر أقل",))
    with c3:
        st.button("🔍 مفقودات", use_container_width=True, key="qa_missing",
                  on_click=_nav_to, args=("🔍 منتجات مفقودة",))
    with c4:
        st.button("⚠️ مراجعة", use_container_width=True, key="qa_review",
                  on_click=_nav_to, args=("⚠️ تحت المراجعة",))
    with c5:
        st.button("✅ معالَجة", use_container_width=True, key="qa_processed",
                  on_click=_nav_to, args=("✅ تمت المعالجة",))


def render(state: AppState, *, on_analyze: Optional[Callable[..., Any]] = None) -> None:
    """يعرض لوحة التحكم كاملة."""
    from ui.components.page_header import render_page_header

    render_page_header("لوحة التحكم", section="dashboard")
    _render_upload(state, on_analyze)
    _render_summary(state)
    _render_banner(state)
    _render_quick_actions(state)
    _render_kpis(state)

    # عند الجلسة الجديدة تكون اللقطة مستعادة بخفة: نعرض ملخصاً دقيقاً من
    # AnalysisResult ونؤجل فك DataFrame الكبيرة ومسوح SQLite إلى أن يفتح المستخدم
    # القسم المختص. كان تنفيذ هذه البطاقات كلها مع الاستعادة الكاملة يكسر حاوية 1GiB.
    if not state.snapshot_frames_fully_loaded:
        import streamlit as st
        st.caption(
            "تُحمَّل التفاصيل الثقيلة عند فتح قسمها لحماية استقرار لوحة التحكم؛ "
            "كل النتائج المحفوظة تبقى متاحة من القائمة الجانبية."
        )
        _render_recent_activity(state)
        return

    _render_quality_report(state)
    _render_quick_stats(state)
    _render_urgent(state)
    _render_urgent_stockouts(state)
    _render_market_digest(state)
    _render_threats(state)
    _render_competitors(state)
    _render_recent_activity(state)
