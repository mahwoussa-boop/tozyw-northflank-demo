# -*- coding: utf-8 -*-
"""scripts/backup_data.py — نسخة أمان لبيت البيانات (قابل لإعادة الاستخدام).

ينسخ إلى data/backups/<اليوم>/: اللقطة ui_session.json + قاعدة pricing_v18.db
(عبر sqlite backup API — قراءة فقط من المصدر، نسخة متسقة حتى مع WAL) + كل ملفات
csv/json الصغيرة في جذر data. يفحص المساحة الحرة أولاً ويتوقف إن كانت ستنزل
تحت الحد الأدنى بعد النسخ. يطبع الحجم + SHA-256 لكل ملف منسوخ.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MIN_FREE_AFTER = 1.5 * 1024**3        # 1.5GB حد أدنى للمساحة الحرة بعد النسخ
SMALL_MAX = 5 * 1024**2               # csv/json «صغير» = أقل من 5MB


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _targets() -> list[Path]:
    # ``ui_session`` صار **مجلّداً** (صيغة 3: ملف لكل إطار) بدل ملف واحد —
    # يُنسَخ كشجرة عبر copytree أدناه. الملف القديم إن بقي يُنسَخ كما كان.
    fixed = [DATA_DIR / "ui_session", DATA_DIR / "ui_session.json",
             DATA_DIR / "pricing_v18.db"]
    small = [p for p in sorted(DATA_DIR.glob("*.csv")) + sorted(DATA_DIR.glob("*.json"))
             if p.is_file() and p.stat().st_size <= SMALL_MAX]
    return [p for p in fixed + small if p.exists()]


def _size(path: Path) -> int:
    """حجم ملف، أو مجموع شجرة مجلّد (لقطة صيغة 3 مجلّد لا ملف)."""
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return path.stat().st_size


def run(dest_root: Path | None = None, label: str = "") -> int:
    targets = _targets()
    total = sum(_size(p) for p in targets)
    free = shutil.disk_usage(DATA_DIR).free
    print(f"المطلوب نسخه: {total/1024**2:,.1f}MB · الحر: {free/1024**3:.2f}GB")
    if free - total < MIN_FREE_AFTER:
        print("⛔ توقف: المساحة الحرة ستنزل تحت 1.5GB بعد النسخ — لم يُنسخ شيء.")
        return 1
    folder = date.today().isoformat() + (f"-{label}" if label else "")
    dest = (dest_root or DATA_DIR / "backups") / folder
    dest.mkdir(parents=True, exist_ok=True)
    for src in targets:
        out = dest / src.name
        if src.is_dir():          # مجلّد اللقطة (صيغة 3): شجرة كاملة
            shutil.copytree(src, out, dirs_exist_ok=True)
            n = sum(1 for f in out.rglob("*") if f.is_file())
            print(f"✅ {out.name:32} {_size(out)/1024**2:9,.2f}MB  ({n} ملفاً)")
            continue
        if src.suffix == ".db":  # نسخة قاعدة متسقة: backup API، المصدر قراءة فقط
            con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            dst = sqlite3.connect(str(out))
            with dst:
                con.backup(dst)
            con.close(); dst.close()
        else:
            shutil.copy2(src, out)
        print(f"✅ {out.name:32} {out.stat().st_size/1024**2:9,.2f}MB  SHA-256={_sha256(out)}")
    print(f"اكتمل النسخ إلى: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(run(label=sys.argv[1] if len(sys.argv) > 1 else ""))
