#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_data_freshness.py — وحدة أ3 خطوة 1: استطلاع نضارة بيانات المنافسين (قراءة فقط).

يقيس عمر updated_at في competitor_products_store لكل متجر منافس، ويقارنه
بعتبة الرادار الحيّة STALE_AFTER_DAYS=3 (services/opportunity_service.py:48)،
ويقرأ آخر جولة كشط لكل متجر من scrape_runs. لا يكتب في أي قاعدة بيانات.
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

DB = str(Path(__file__).resolve().parent.parent / "data" / "pricing_v18.db")
STALE_AFTER_DAYS = 3


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    now = datetime.now()

    cur.execute("""
        SELECT competitor, updated_at, availability
        FROM competitor_products_store
    """)
    rows = cur.fetchall()

    by_store: dict[str, list[float]] = {}
    null_updated: dict[str, int] = {}
    for r in rows:
        store = r["competitor"] or "؟"
        ts = _parse_ts(r["updated_at"])
        if ts is None:
            null_updated[store] = null_updated.get(store, 0) + 1
            continue
        age_days = (now - ts).total_seconds() / 86400
        by_store.setdefault(store, []).append(age_days)

    cur.execute("""
        SELECT competitor, MAX(run_at) AS last_run, COUNT(*) AS n_runs
        FROM scrape_runs GROUP BY competitor
    """)
    last_run = {r["competitor"]: (r["last_run"], r["n_runs"]) for r in cur.fetchall()}

    print("=== خريطة نضارة بيانات المنافسين — عتبة الرادار الحيّة: "
          f"{STALE_AFTER_DAYS} أيام ===\n")

    total_rows = sum(len(v) for v in by_store.values())
    total_stale = 0
    total_null = sum(null_updated.values())

    header = f"{'المتجر':20s} {'الصفوف':>8s} {'أحدث(يوم)':>10s} {'وسيط(يوم)':>10s} {'أقدم(يوم)':>10s} {'قديم>%dي' % STALE_AFTER_DAYS:>10s} {'بلا تاريخ':>10s} {'آخر كشط':>12s}"
    print(header)
    print("-" * len(header))

    for store in sorted(by_store, key=lambda s: -len(by_store[s])):
        ages = by_store[store]
        n = len(ages)
        stale_n = sum(1 for a in ages if a > STALE_AFTER_DAYS)
        total_stale += stale_n
        run_at, n_runs = last_run.get(store, (None, 0))
        run_age = ""
        if run_at:
            rt = _parse_ts(run_at)
            if rt:
                run_age = f"{(now - rt).days}يوم"
        print(f"{store:20s} {n:8d} {min(ages):10.1f} {statistics.median(ages):10.1f} "
              f"{max(ages):10.1f} {100*stale_n/n:9.1f}% {null_updated.get(store, 0):10d} {run_age:>12s}")

    print(f"\n--- خلاصة إجمالية ---")
    print(f"  إجمالي صفوف بتاريخ صالح: {total_rows}")
    print(f"  صفوف قديمة (> {STALE_AFTER_DAYS} أيام): {total_stale} ({100*total_stale/max(total_rows,1):.1f}%)")
    print(f"  صفوف بلا تاريخ تحديث إطلاقاً (updated_at فارغ): {total_null}")
    print(f"  عدد متاجر المنافسين المرصودة: {len(by_store) + len(set(null_updated) - set(by_store))}")


if __name__ == "__main__":
    main()
