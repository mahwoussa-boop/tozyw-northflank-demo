# -*- coding: utf-8 -*-
"""scripts/prune_signal_events.py — تدوير جدول ``product_signal_events``.

المشكلة (اكتُشفت 2026-07-22): الجدول append-only صرف (سجل أحداث تحوّل
نفاد/عودة/تغيّر سعر يكتبه ``engines/mahally_scraper.py``) بلا أي DELETE سابق
منذ إنشائه — تراكم 273 ألف صف خلال 19 يوماً فقط من التشغيل الفعلي. القراءات
الحيّة كلها (alert_service/volatility_service/priority_engine) تنظر بنافذة
أيام قليلة فقط، فحذف الصفوف الأقدم من ``RETENTION_DAYS`` لا يفقد أي وظيفة حيّة.

آمن: DELETE محدود بشرط زمني وحده داخل معاملة صريحة، لا يمسّ أي جدول آخر ولا
أي سجلّ/قرار/تصدير. يُشغَّل من ``rebuild_radar.bat`` (مهمة 04:30) بعد إعادة
بناء كاش الرادار — منفصل عمداً عن ``rebuild_radar.py`` كي يبقى وعده الحالي
("لا يمسّ أي سجلّ/أرشيف") صحيحاً بلا تعديل.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.data_paths import get_data_db_path  # noqa: E402

RETENTION_DAYS = 90


def prune_signal_events(db_path: str, days: int = RETENTION_DAYS) -> int:
    """يحذف صفوف ``product_signal_events`` الأقدم من ``days`` يوماً. يعيد عدد المحذوف."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute("BEGIN")
            cur = conn.execute(
                "DELETE FROM product_signal_events WHERE run_at < datetime('now', ?)",
                (f"-{int(days)} days",),
            )
            deleted = cur.rowcount
        return deleted
    finally:
        conn.close()


def main() -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    db_path = get_data_db_path("pricing_v18.db")
    print(f"[{stamp}] prune_signal_events | db={db_path} | retention={RETENTION_DAYS} يوم")
    deleted = prune_signal_events(db_path)
    print(f"  🗑️ حُذف {deleted} صفاً أقدم من {RETENTION_DAYS} يوماً.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
