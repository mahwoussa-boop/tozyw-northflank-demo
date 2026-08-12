# -*- coding: utf-8 -*-
"""scripts/shadow_pricing_report.py — تقرير السياج الظلّي (قراءة فقط) — وحدة أ1.

يقرأ جدول ``pricing_guard_shadow`` (المكتوب INSERT-صرف من ``services/pricing_shadow.py``
عند كل إرسال فعلي) ويلخّص أحكام الظلّ دون أي كتابة أو إرسال أو لمس شبكة. الغاية:
إعطاء المالك صورة رقمية خام عن «ماذا كانت سياسة v1 ستفعل» قبل أي قرار فرض (بوّابة م3).

**قراءة فقط تماماً:** الاتصال ``mode=ro`` (الكتابة تفشل على مستوى SQLite)، لا DML/DDL،
لا إرسال، لا شبكة. آمن على المتجر الحيّ بالكامل.

الأحكام الخمسة الحقيقية (مصدرها ``pricing_shadow.verdict_for`` — لا تُخترع):
  • ok            — السعر المُرسَل داخل ±20% من وسيط السوق (مطابق لسياسة v1).
  • below_band    — أقل من 80% من الوسيط (تسعير منخفض: هامش متروك أو خطأ كشط سحب السعر).
  • above_band    — أعلى من 120% من الوسيط (تسعير مرتفع: خطر فقد مبيعات).
  • stale_market  — بيانات المنافس أقدم من حارس الطزاجة ⇒ لا حكم سعري موثوق.
  • no_market_row — لا صفّ سوق مطابق (لا وسيط) ⇒ لا مرجع للمقارنة.

بوّابة م3 (الفرض): تحتاج ~200 صفّ ظلّ حقيقي قبل تقديم تقرير verdicts للمالك ليقرّر الفرض.

الاستعمال:
  .venv/Scripts/python.exe -m scripts.shadow_pricing_report        # القاعدة الافتراضية
  .venv/Scripts/python.exe -m scripts.shadow_pricing_report <path> # قاعدة محدّدة
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # ضمان طباعة عربية سليمة على كونسول Windows (cp1256)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

# بوّابة الفرض الموثّقة (م3) — عدد صفوف الظلّ اللازم قبل تقرير الفرض للمالك.
GATE_ROWS = 200

# ترتيب عرض ثابت للأحكام الخمسة (نفس تعداد pricing_shadow — لا يُخترع حكم).
VERDICT_ORDER = ("ok", "below_band", "above_band", "stale_market", "no_market_row")
VERDICT_AR = {
    "ok": "مطابق للسياسة (داخل النطاق)",
    "below_band": "أقل من النطاق (تسعير منخفض)",
    "above_band": "أعلى من النطاق (تسعير مرتفع)",
    "stale_market": "بيانات قديمة (لا حكم)",
    "no_market_row": "لا صفّ سوق (لا مرجع)",
}


def _resolve_db(argv: list[str]) -> str:
    """مسار القاعدة: وسيط سطر الأوامر إن وُجد، وإلا المسار الافتراضي من pricing_shadow
    (مصدر واحد للحقيقة — لا مسار ثابت مكرّر)."""
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    from services.pricing_shadow import _default_db
    return _default_db()


def _fmt_sar(v: Optional[float]) -> str:
    return f"{v:,.2f} ر.س" if isinstance(v, (int, float)) else "—"


def _summarize_gap(rows: list[tuple]) -> None:
    """يلخّص فجوة (السعر المُرسَل − السعر المقترَح) بالريال لصفوف below/above.
    ``rows`` عناصرها (sent_price, suggested_price)."""
    gaps = [
        float(sent) - float(sug)
        for sent, sug in rows
        if sent is not None and sug is not None
    ]
    if not gaps:
        print("      (لا فجوات قابلة للحساب — أسعار مقترَحة غائبة)")
        return
    gaps_abs = [abs(g) for g in gaps]
    print(
        f"      فجوة السعر (مُرسَل − مقترَح): "
        f"وسيط={statistics.median(gaps_abs):,.2f} ر.س · "
        f"أقصى={max(gaps_abs):,.2f} ر.س · "
        f"إجمالي مطلق={sum(gaps_abs):,.2f} ر.س · n={len(gaps)}"
    )


def main() -> int:
    db_path = _resolve_db(sys.argv)
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"

    print("═" * 64)
    print("تقرير السياج الظلّي (قراءة فقط) — وحدة أ1")
    print(f"القاعدة: {db_path}")
    print("═" * 64)

    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        print(f"⚠️  تعذّر فتح القاعدة (قراءة فقط): {e}")
        print("    لا شيء يُكتب — تحقّق من المسار وأعد التشغيل.")
        return 0

    try:
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='pricing_guard_shadow'"
        ).fetchone()
        if not exists:
            print("ℹ️  جدول pricing_guard_shadow غير موجود بعد في هذه القاعدة.")
            print("    يُنشأ تلقائياً عند أول إرسال فعلي (services/pricing_shadow.py).")
            print("    شغّل هذا التقرير على قاعدة الإنتاج بعد تجمّع إرسالات حقيقية.")
            return 0

        total = con.execute("SELECT COUNT(*) FROM pricing_guard_shadow").fetchone()[0]
        if total == 0:
            print("ℹ️  الجدول موجود لكنه فارغ (0 صفّ) — لا إرسالات مُدوَّنة بعد.")
            return 0

        # ── 1) التوزيع حسب الحكم ──
        by_verdict = dict(
            con.execute(
                "SELECT verdict, COUNT(*) FROM pricing_guard_shadow GROUP BY verdict"
            ).fetchall()
        )
        print(f"\n═══ 1) توزيع الأحكام (إجمالي {total:,} صفّ) ═══")
        seen = 0
        for v in VERDICT_ORDER:
            n = int(by_verdict.get(v, 0))
            seen += n
            pct = (n / total * 100) if total else 0.0
            print(f"  {v:<14} {VERDICT_AR[v]:<28} {n:>6,}  ({pct:5.1f}%)")
        # أي حكم غير متوقّع (حماية من انحراف صامت في التعداد)
        for v, n in by_verdict.items():
            if v not in VERDICT_ORDER:
                print(f"  ⚠️ حكم غير متوقّع: {v!r} = {n:,} — راجع pricing_shadow.verdict_for")
                seen += int(n)
        if seen != total:
            print(f"  ⚠️ تباين: مجموع الأحكام {seen:,} ≠ الإجمالي {total:,}")

        # ── 2) الأثر الاقتصادي لصفوف خارج النطاق ──
        print("\n═══ 2) الأثر الاقتصادي (صفوف خارج النطاق فقط) ═══")
        for v in ("below_band", "above_band"):
            n = int(by_verdict.get(v, 0))
            print(f"  • {v} ({VERDICT_AR[v]}) — {n:,} صفّ:")
            if n:
                rows = con.execute(
                    "SELECT sent_price, suggested_price FROM pricing_guard_shadow "
                    "WHERE verdict=?",
                    (v,),
                ).fetchall()
                _summarize_gap(rows)

        # ── 3) طزاجة البيانات ──
        print("\n═══ 3) عمر بيانات السوق (data_age_days) ═══")
        ages = [
            float(r[0])
            for r in con.execute(
                "SELECT data_age_days FROM pricing_guard_shadow "
                "WHERE data_age_days IS NOT NULL"
            ).fetchall()
        ]
        if ages:
            print(
                f"  n={len(ages):,} · أدنى={min(ages):.1f} يوم · "
                f"وسيط={statistics.median(ages):.1f} يوم · أقصى={max(ages):.1f} يوم"
            )
        else:
            print("  (لا قيم عمر مُدوَّنة)")

        # ── 4) بوّابة الفرض م3 ──
        print("\n═══ 4) بوّابة الفرض (م3) ═══")
        pct = min(total / GATE_ROWS * 100, 100.0)
        status = "✅ بلغت البوّابة" if total >= GATE_ROWS else "⏳ لم تبلغ بعد"
        print(f"  {total:,} / {GATE_ROWS} صفّ  ({pct:.0f}%)  {status}")
        if total < GATE_ROWS:
            print(f"  ينقص {GATE_ROWS - total:,} صفّ إرسال حقيقي قبل تقرير الفرض للمالك.")

        print("\n" + "═" * 64)
        print("انتهى — قراءة صرفة، لا شيء كُتب أو أُرسل.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
