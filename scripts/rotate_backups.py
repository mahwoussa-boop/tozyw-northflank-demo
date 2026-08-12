# -*- coding: utf-8 -*-
"""scripts/rotate_backups.py — تدوير مجلدات ``data/backups/`` اليومية (قرار المالك 2026-07-22: آخر 7).

يستهدف حصراً المجلدات المؤرَّخة بنمط ``YYYY-MM-DD[-تسمية]`` (نواتج
``backup_data.py`` اليومية/اليدوية) — **لا يمسّ** الملفات المتفرقة الأقدم
خارج هذا النمط (`*.db`/`*.json` يتيمة من هجرات سابقة) — تلك تحتاج جرداً
وموافقة مالك منفصلة، ليست جزءاً من هذه السياسة. يُشغَّل من
``backup_daily.bat`` بعد نجاح كل نسخة جديدة فقط. لا حذف إن كان العدد ضمن
الحدّ.
"""
from __future__ import annotations

import re
import shutil
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

KEEP = 7
_DATED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def rotate_backups(backups_dir: str, keep: int = KEEP) -> list[str]:
    """يحذف أقدم مجلدات النسخ المؤرَّخة إن تجاوز عددها ``keep``. يعيد أسماء المحذوف."""
    root = Path(backups_dir)
    if not root.is_dir():
        return []
    dated = sorted(
        (p for p in root.iterdir() if p.is_dir() and _DATED_DIR_RE.match(p.name)),
        key=lambda p: p.name,
    )
    to_delete = dated[:-keep] if keep > 0 else dated
    deleted: list[str] = []
    for p in to_delete:
        shutil.rmtree(p, ignore_errors=True)
        deleted.append(p.name)
    return deleted


def main() -> int:
    from utils.data_paths import get_data_dir
    backups_dir = str(Path(get_data_dir()) / "backups")
    stamp = datetime.now().isoformat(timespec="seconds")
    deleted = rotate_backups(backups_dir, KEEP)
    print(f"[{stamp}] rotate_backups | dir={backups_dir} | keep={KEEP}")
    if deleted:
        print(f"  🗑️ حُذف {len(deleted)} مجلداً: {', '.join(deleted)}")
    else:
        print("  لا حذف — العدد ضمن الحدّ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
