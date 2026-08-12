# -*- coding: utf-8 -*-
"""scripts/redistribution_dryrun.py — بروفة جافة لإعادة توزيع المفقودات (قراءة فقط).

يقرأ data/ui_session.json ويطبّق services.reclassify_missing.propose على صفوف
review دون كتابة أي شيء — تقرير نصي فقط لموافقة المالك قبل أي تطبيق فعلي.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.reclassify_missing import bucket_of, build_our_brand_index, propose

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "ui_session.json"
BUCKETS = ("R1", "R2", "R3", "R4")
_RANGES = ((0, 55), (55, 65), (65, 82), (82, 101))


def _score_range(s: float) -> str:
    for lo, hi in _RANGES:
        if lo <= s < hi:
            return f"[{lo}-{hi})"
    return "خارج النطاق"


def main() -> int:
    # اللقطة صارت مجلّداً (صيغة 3) — جسر التوافق يعيد نفس البنية الخام القديمة.
    from ui.state_manager import load_snapshot_raw

    snap = load_snapshot_raw()
    md, oc = snap["missing_df"], snap.get("our_catalog")
    our_brands = build_our_brand_index(oc)
    green_n = int((md["مستوى_الثقة"] == "green").sum())
    review = md[md["مستوى_الثقة"] == "review"]
    print(f"اللقطة: missing_df={len(md):,} · green={green_n:,} · review={len(review):,}")
    print(f"فهرس ماركات كتالوجنا: {len(our_brands):,} اسم منتج ← ماركة\n")

    print("═══ 1) تكرار حالة_المراجعة × نطاقات الدرجات (صفوف review) ═══")
    cross: Counter = Counter()
    for _, r in review.iterrows():
        try:
            s = float(r.get("درجة_التشابه") or 0)
        except (TypeError, ValueError):
            s = 0.0
        cross[(r.get("حالة_المراجعة") or "<فارغ>", _score_range(s))] += 1
    for (reason, rng), n in sorted(cross.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:28} × {rng:9} = {n:6,}")

    rows_by_bucket: dict[str, list] = defaultdict(list)
    r3_resolved = 0
    for _, r in review.iterrows():
        row = r.to_dict()
        res = propose(row, our_brands)
        b = bucket_of(row, res)
        rows_by_bucket[b].append((row, res))
        if 65 <= float(row.get("درجة_التشابه") or 0) < 82 and \
                our_brands.get(str(row.get("منتج_مطابق_محتمل") or "").strip()):
            r3_resolved += 1

    print(f"\n═══ 2) عدّ السلال (من أصل {len(review):,} صف review) ═══")
    total = 0
    for b in BUCKETS:
        n = len(rows_by_bucket[b])
        total += n
        print(f"  {b}: {n:6,}  ({n / len(review) * 100:5.1f}%)")

    print("\n═══ 3) عشر عينات لكل سلة ═══")
    for b in BUCKETS:
        print(f"\n--- {b} ---")
        for row, (lvl, reason, prio) in rows_by_bucket[b][:10]:
            print(f"  • {str(row.get('منتج_المنافس'))[:52]:54} | "
                  f"درجة={float(row.get('درجة_التشابه') or 0):5.1f} | "
                  f"السبب={reason or str(row.get('حالة_المراجعة'))[:24]:26} | "
                  f"ماركة={str(row.get('الماركة'))[:14]:16} | "
                  f"مطابق={str(row.get('منتج_مطابق_محتمل'))[:34]:36} | "
                  f"منافسون={int(float(row.get('عدد_المنافسين') or 0))}")

    in_r3_range = sum(n for (_, rng), n in cross.items() if rng == "[65-82)")
    print(f"\n═══ 4) تغطية حل الماركة في نطاق R3 ═══")
    print(f"  صفوف نطاق [65-82): {in_r3_range:,} · أمكن حل ماركة مطابقها: {r3_resolved:,} "
          f"({(r3_resolved / in_r3_range * 100) if in_r3_range else 0:.1f}%)")

    print("\n═══ 5) توزيع هو_تستر ونوع_السلعة عبر السلال ═══")
    for b in BUCKETS:
        rows = [row for row, _ in rows_by_bucket[b]]
        testers = sum(1 for r in rows if str(r.get("هو_تستر")).lower() in ("true", "1"))
        kinds = Counter(str(r.get("نوع_السلعة") or "؟") for r in rows)
        kinds_txt = " · ".join(f"{k}={v:,}" for k, v in kinds.most_common())
        print(f"  {b}: تستر={testers:,} | {kinds_txt}")

    ok = (total == len(review)) and (green_n == 7049) and (len(review) == 23668)
    print(f"\n═══ 6) سطر المطابقة الإلزامي ═══")
    print(f"  مجموع السلال={total:,} (المطلوب 23,668) · green الحالي={green_n:,} "
          f"(المطلوب 7,049 بلا تغيير) ⇒ {'✅ متطابق' if ok else '❌ غير متطابق'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
