# -*- coding: utf-8 -*-
"""scripts/report_c5_brand_category_fixes.py — بروفة جافة لتصحيح 4 أخطاء ماركة/تصنيف (وحدة ج5، قراءة فقط).

يقرأ data/ui_session.json (لا يكتب شيئاً) ويبحث عن الحالات الأربع المؤكَّدة في
docs/خطة-التطوير-الشاملة.md §ج5 داخل ``our_catalog`` الحيّة، يطبع قيمها الحالية
(الماركة/تصنيف المنتج) والتصحيح المقترح لكل حالة — تقرير نصي فقط لموافقة المالك
قبل أي كتابة فعلية على اللقطة أو أي تصدير لسلة.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.state_manager import _deserialize_snapshot  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "ui_session.json"

# كل حالة: (تسمية، نص بحث في الاسم لتضييق المرشَّحين، شرط دقيق يحدّد التناقض
#  الفعلي على الصفّ لا مجرّد تطابق الاسم — لتفادي إيجابيات كاذبة على منتجات
#  مشابهة الاسم لكنها مصنَّفة بشكل صحيح فعلاً، الماركة المقترحة أو None،
#  التصنيف المقترح أو None، السبب).
def _c1_condition(row: Any, name: str) -> bool:
    return "برجوا" in name and str(row.get("الماركة", "")).strip() in ("بدون ماركة", "", "None", "nan")


def _c2_condition(row: Any, name: str) -> bool:
    cat = str(row.get("تصنيف المنتج", ""))
    return "للجنسين" in name and "نسائية" in cat and "رجالية" not in cat


def _c3_condition(row: Any, name: str) -> bool:
    cat = str(row.get("تصنيف المنتج", ""))
    return "رجالي" in name and "نسائية" in cat


def _c4_condition(row: Any, name: str) -> bool:
    brand = str(row.get("الماركة", ""))
    return "آيسنوب" in name and ("بوزو" in brand or "Pozo" in brand)


CASES = [
    ("برجوا احمر خدود", _c1_condition, "برجوا | Bourjois", None,
     "الاسم يذكر «برجوا» (Bourjois — علامة حقيقية معروفة) لكن الماركة المسجَّلة"
     " «بدون ماركة»"),
    ("زيرجوف مفيستو", _c2_condition, None, None,
     "الاسم يذكر «للجنسين» صراحة لكن التصنيف الحالي «نسائية» فقط دون «رجالية» —"
     " تناقض مباشر (التصحيح الدقيق يحتاج معرفة القيم المسموحة الفعلية لعمود"
     " التصنيف في كتالوجك على سلة قبل اقتراح نص بديل)"),
    ("مونت بلانك ليجند", _c3_condition, None, None,
     "الاسم يذكر «رجالي» صراحة لكن التصنيف الحالي يحوي «نسائية» —"
     " تناقض مباشر (نفس ملاحظة الحالة السابقة بخصوص القيم المسموحة)"),
    ("آيسنوب", _c4_condition, "آيسنوب | ISNUP", None,
     "الماركة المسجَّلة تحمل «بوزو/Pozo» (دار عطور لا علاقة لها"
     " بمبخرة كهربائية) — تصادم بيانات واضح"),
]


def main() -> int:
    # اللقطة صارت مجلّداً (صيغة 3) — جسر التوافق يعيد نفس البنية الخام القديمة.
    from ui.state_manager import load_snapshot_raw, snapshot_exists

    if not snapshot_exists():
        print(f"❌ لا لقطة حيّة عند {SNAPSHOT}")
        return 1
    snap = _deserialize_snapshot(load_snapshot_raw())
    oc = snap.get("our_catalog")
    if oc is None or not hasattr(oc, "columns"):
        print("❌ لا عمود our_catalog في اللقطة")
        return 1
    print(f"اللقطة: our_catalog = {len(oc):,} صفاً")
    name_col = next(
        (c for c in ("المنتج", "أسم المنتج", "اسم المنتج", "product_name") if c in oc.columns),
        None,
    )
    if name_col is None:
        print(f"❌ لا عمود اسم منتج معروف — الأعمدة المتاحة: {list(oc.columns)[:15]}")
        return 1
    print(f"عمود الاسم المستخدَم: «{name_col}»\n")

    for needle, condition, prop_brand, prop_cat, reason in CASES:
        name_matches = oc[oc[name_col].astype(str).str.contains(needle, na=False)]
        flagged, cleared = [], []
        for idx, row in name_matches.iterrows():
            name = str(row.get(name_col))
            (flagged if condition(row, name) else cleared).append((idx, row))
        print(f"═══ بحث: «{needle}» — {len(name_matches)} صف بالاسم،"
              f" {len(flagged)} يستوفي شرط التناقض الفعلي ═══")
        if not flagged:
            print("  ⚠️ لا صفّ يستوفي الشرط الآن — قد تكون البيانات تغيّرت منذ توثيق"
                  " الخطة (2026-07-22)؛ يحتاج تحققاً يدوياً قبل أي تصحيح.\n")
            continue
        for idx, row in flagged:
            cur_brand = row.get("الماركة", "؟")
            cur_cat = row.get("تصنيف المنتج", "؟")
            pid = row.get("معرف_المنتج") or row.get("رقم_المنتج") or row.get("No.") or idx
            print(f"  • [{pid}] {str(row.get(name_col))[:60]}")
            print(f"      الماركة الحالية:  {cur_brand}")
            print(f"      التصنيف الحالي:   {cur_cat}")
            print(f"      المقترَح (ماركة): {prop_brand or '— بلا تغيير مقترَح تلقائياً —'}")
            print(f"      المقترَح (تصنيف): {prop_cat or '— بلا تغيير مقترَح تلقائياً —'}")
            print(f"      السبب: {reason}")
        if cleared:
            print(f"  ✓ {len(cleared)} صف بنفس الاسم الفرعي لكن مصنَّف بشكل صحيح فعلاً"
                  " — لم يُدرَج (تفادي إيجابية كاذبة):")
            for idx, row in cleared[:5]:
                pid = row.get("معرف_المنتج") or idx
                print(f"      - [{pid}] {str(row.get(name_col))[:55]}"
                      f" | تصنيف: {str(row.get('تصنيف المنتج'))[:40]}")
        print()
    print("⚠️ هذا تقرير قراءة فقط — صفر كتابة على اللقطة أو القاعدة أو أي تصدير لسلة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
