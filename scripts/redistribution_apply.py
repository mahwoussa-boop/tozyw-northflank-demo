# -*- coding: utf-8 -*-
"""scripts/redistribution_apply.py — تطبيق إعادة توزيع المفقودات (كتابة واحدة).

يُشغَّل **مرة واحدة** وتطبيق Streamlit مغلق. يحمّل اللقطة عبر StateManager الرسمي،
يعيد حساب السلال بنفس ``propose``، يقارنها حرفياً بأرقام البروفة، يرقّي R1/R2/R3
إلى green، يُصلح تفتيت أسماء المتاجر لمرة واحدة، يتحقق من الثوابت الصارمة، ثم يحفظ
حفظاً واحداً عبر ``persist_results``. أي انحراف ⇒ إجهاض فوري بلا كتابة.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conf.constants import COMPETITOR_DB_PATH
from services.reclassify_missing import build_our_brand_index, bucket_of, propose
from ui.components.comparison_card import _restore_stores  # #PRESERVED_LOGIC (قراءة فقط)
from ui.state_manager import AppState

EXPECTED = {"R1": 8251, "R2": 634, "R3": 5147, "R4": 9636}
FINAL_GREEN, FINAL_REVIEW, TOTAL, ORIG_GREEN = 21081, 9636, 30717, 7049


def _abort(msg: str) -> "NoReturn":
    print(f"\n⛔ إجهاض بلا كتابة: {msg}")
    sys.exit(1)


def _canonical_map() -> dict[str, str]:
    con = sqlite3.connect(f"file:{COMPETITOR_DB_PATH}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT DISTINCT competitor FROM competitor_products_store") if r[0]]
    finally:
        con.close()
    return {n.replace(" ", ""): n for n in names}


def main() -> int:
    from ui.state_manager import snapshot_size_bytes  # اللقطة صارت مجلّداً (صيغة 3)

    size_before = snapshot_size_bytes()
    print(f"═══ حجم اللقطة قبل: {size_before/1024**2:,.2f}MB ═══")

    # a) تحميل عبر StateManager الرسمي
    state = AppState()
    if not state.restore_results():
        _abort("تعذّر تحميل اللقطة عبر restore_results")
    md = state.missing_df
    if md is None or md.empty:
        _abort("missing_df فارغ")
    print(f"a) حُمّلت اللقطة: missing_df={len(md):,}")

    orig_green_idx = set(md.index[md["مستوى_الثقة"] == "green"])
    if len(orig_green_idx) != ORIG_GREEN:
        _abort(f"green الأصلية {len(orig_green_idx):,} ≠ {ORIG_GREEN:,}")
    our_brands = build_our_brand_index(state.our_catalog)

    # b) إعادة حساب السلال والمقارنة الحرفية
    review_idx = list(md.index[md["مستوى_الثقة"] == "review"])
    plans: dict = {}
    counts = {"R1": 0, "R2": 0, "R3": 0, "R4": 0}
    for i in review_idx:
        row = md.loc[i].to_dict()
        res = propose(row, our_brands)
        b = bucket_of(row, res)
        plans[i] = (b, res, row.get("حالة_المراجعة", ""))
        counts[b] += 1
    print(f"b) السلال: {counts}")
    if counts != EXPECTED:
        _abort(f"انحراف عن البروفة: {counts} ≠ {EXPECTED}")
    print("   ✅ مطابقة حرفية لأرقام البروفة (8,251 / 634 / 5,147 / 9,636)")

    # c) الترقية + عمودان جديدان
    for col in ("حالة_سابقة", "سبب_النقل"):
        if col not in md.columns:
            md[col] = ""
    if "أولوية_المراجعة" not in md.columns:
        md["أولوية_المراجعة"] = 0
    promoted = 0
    for i, (b, res, old_reason) in plans.items():
        level, reason, prio = res
        if b in ("R1", "R2", "R3"):
            md.at[i, "حالة_سابقة"] = f"review|{old_reason}"
            md.at[i, "سبب_النقل"] = reason
            md.at[i, "مستوى_الثقة"] = "green"
            md.at[i, "حالة_المراجعة"] = ""
            promoted += 1
        else:  # R4 يبقى review
            md.at[i, "أولوية_المراجعة"] = int(prio)
    print(f"c) رُقّي إلى green: {promoted:,} · بقي review: {counts['R4']:,}")

    # d) إصلاح تفتيت أسماء المتاجر لمرة واحدة (كل الصفوف المصابة)
    canon = _canonical_map()
    fixed = unmatched = 0
    for i in md.index:
        if len(str(md.at[i, "المنافس"] or "").strip()) > 1:
            continue  # ليس مفتّتاً (اسم كامل)
        rebuilt = _restore_stores(md.at[i, "المنافسون"])
        recov = [canon.get(s.replace(" ", ""), s) for s in rebuilt]  # غير المطابق يبقى كما رُمّم
        if any(canon.get(s.replace(" ", "")) for s in rebuilt):
            md.at[i, "المنافس"] = recov[0]
            md.at[i, "المنافسون"] = "، ".join(recov)
            fixed += 1
        elif rebuilt:  # رُمّم لكن لم يطابق أي اسم قانوني
            md.at[i, "المنافس"] = recov[0]
            md.at[i, "المنافسون"] = "، ".join(recov)
            unmatched += 1
    print(f"d) أسماء متاجر مُصلَحة: {fixed:,} · تعذّرت مطابقتها (تبقى كما رُمّمت): {unmatched:,}")

    # e) سطر التحقق الإلزامي قبل الحفظ
    g = int((md["مستوى_الثقة"] == "green").sum())
    r = int((md["مستوى_الثقة"] == "review").sum())
    n = len(md)
    orig_ok = all(md.at[i, "مستوى_الثقة"] == "green" for i in orig_green_idx)
    orig_untouched = all(md.at[i, "سبب_النقل"] == "" for i in orig_green_idx)
    print(f"e) تحقق: green={g:,} review={r:,} إجمالي={n:,} "
          f"green_أصلية_سليمة={orig_ok and orig_untouched}")
    if g != FINAL_GREEN:
        _abort(f"green={g:,} ≠ {FINAL_GREEN:,}")
    if r != FINAL_REVIEW:
        _abort(f"review={r:,} ≠ {FINAL_REVIEW:,}")
    if n != TOTAL:
        _abort(f"إجمالي={n:,} ≠ {TOTAL:,} (صفوف مفقودة!)")
    if not (orig_ok and orig_untouched):
        _abort("green الأصلية 7,049 مُسّت")
    print("   ✅ كل الثوابت سليمة")

    # f) حفظ واحد عبر persist الرسمي
    state.missing_df = md
    if not state.persist_results():
        _abort("فشل persist_results")
    size_after = snapshot_size_bytes()
    print(f"f) ✅ حُفظت اللقطة · الحجم بعد: {size_after/1024**2:,.2f}MB "
          f"(تغيّر {(size_after-size_before)/1024**2:+,.2f}MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
