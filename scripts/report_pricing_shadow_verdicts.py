#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_pricing_shadow_verdicts.py — تقرير وحدة أ1 (قراءة فقط، صفر سطر إنتاجي).

يحاكي core.pricing_guard.apply_policy_v1 الحقيقية على كل صف حيّ في
pricing_guard_shadow، مستخدماً price_median/instock_price_min المخزَّنين
(الجدول لا يخزّن قائمة أسعار المنافسين الخام). يُنشئ نقطتين اصطناعيتين
{instock_price_min, 2*price_median-instock_price_min} تُعيدان بالضبط نفس
min/median الحقيقيين لأي بيانات صحيحة (median≥min دائماً) — فالدالة الحقيقية
تُستدعى بلا تعديل، لا إعادة تنفيذ لمنطقها.

لا يكتب في أي قاعدة بيانات. mode=ro فقط.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from core.pricing_guard import apply_policy_v1

DB = str(Path(__file__).resolve().parent.parent / "data" / "pricing_v18.db")


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT s.sku, s.norm_name, s.sent_price, s.price_median, s.instock_price_min,
               s.data_age_days, s.verdict AS shadow_verdict, c.cost_price
        FROM pricing_guard_shadow s
        LEFT JOIN our_catalog c ON c.norm_name = s.norm_name
    """)
    rows = cur.fetchall()

    v1_counts: Counter = Counter()
    shadow_counts: Counter = Counter()
    diffs = []  # (abs diff, sku, sent, v1_price)

    for r in rows:
        shadow_counts[r["shadow_verdict"]] += 1
        median = r["price_median"]
        min_p = r["instock_price_min"]
        cost = r["cost_price"] or 0.0

        if median is None or min_p is None:
            v1_counts["no_available_market"] += 1
            continue

        p2 = 2 * median - min_p
        result = apply_policy_v1(
            competitor_prices=[
                {"price": min_p, "available": True},
                {"price": p2, "available": True},
            ],
            cost_price=cost,
        )
        v1_counts[result["verdict"]] += 1

        if r["sent_price"] is not None and result["price"] is not None:
            d = abs(r["sent_price"] - result["price"])
            diffs.append((d, r["sku"], r["sent_price"], result["price"]))

    total = len(rows)
    print(f"=== تقرير الظل السعري — {total} صف حيّ من pricing_guard_shadow ===\n")

    print("--- تبويب شفرة الظل الأصلية (5 أنواع، بلا أرضية تكلفة) ---")
    for k, v in shadow_counts.most_common():
        print(f"  {k:20s} {v:5d}  ({100*v/total:.1f}%)")

    print("\n--- محاكاة apply_policy_v1 الحقيقية (6 أنواع، مع أرضية التكلفة) ---")
    for k, v in v1_counts.most_common():
        print(f"  {k:24s} {v:5d}  ({100*v/total:.1f}%)")

    diffs.sort(reverse=True)
    print(f"\n--- أكبر 10 فروق بين السعر المُرسَل فعلياً وسعر سياسة v1 ---")
    for d, sku, sent, v1p in diffs[:10]:
        print(f"  sku={sku:16s} مُرسَل={sent:>9.2f}  v1={v1p:>9.2f}  فرق={d:>8.2f}")

    n_would_change = sum(1 for d, *_ in diffs if d > 0.01)
    print(f"\n--- خلاصة ---")
    print(f"  إجمالي الصفوف: {total}")
    print(f"  فيها سعر مُرسَل وسعر v1 قابلان للمقارنة: {len(diffs)}")
    print(f"  كانت ستتغيّر (فرق>0.01): {n_would_change} ({100*n_would_change/max(len(diffs),1):.1f}%)")
    print(f"  بلا سوق صالح للمقارنة (no_available_market): {v1_counts.get('no_available_market', 0)}")


if __name__ == "__main__":
    main()
