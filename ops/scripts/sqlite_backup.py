#!/usr/bin/env python3
"""إنشاء أرشيفات SQLite متسقة لتطبيق Tozyw.

البرنامج لا يرسل أي بيانات ولا يدير أسراراً. تُرفع الأرشيفات لاحقاً عبر rclone
إلى remote من نوع crypt مُعد مسبقاً، حتى لا ينتقل ملف SQLite بصيغته الصريحة.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone


class BackupError(RuntimeError):
    """خطأ متوقع يمنع إنشاء نسخة احتياطية موثوقة."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create consistent SQLite backup archives.")
    parser.add_argument(
        "--database",
        action="append",
        required=True,
        type=Path,
        help="مسار قاعدة SQLite. كرر الخيار لكل قاعدة إضافية.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="المجلد المحلي الذي تحفظ فيه الأرشيفات قبل الرفع.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help="عدد الأيام للاحتفاظ بالأرشيفات المحلية (الافتراضي: 30).",
    )
    parser.add_argument(
        "--label",
        default="tozyw",
        help="بادئة آمنة لاسم الأرشيف (الافتراضي: tozyw).",
    )
    return parser.parse_args()


def require_safe_label(label: str) -> str:
    if not label or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in label):
        raise BackupError("يجب أن يحتوي --label على حروف وأرقام وشرطة فقط.")
    return label


@contextlib.contextmanager
def exclusive_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("توجد عملية نسخ احتياطي أخرى قيد التشغيل؛ تم الإلغاء بأمان.") from exc
        yield


def sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    """خذ لقطة عبر SQLite Backup API، المتوافقة مع قواعد WAL النشطة."""
    if not source_path.is_file():
        raise BackupError(f"قاعدة البيانات غير موجودة أو ليست ملفاً: {source_path}")

    source_uri = f"file:{source_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(destination_path) as destination:
            source.backup(destination)
        with sqlite3.connect(f"file:{destination_path}?mode=ro", uri=True) as check:
            result = check.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"فشلت لقطة SQLite أو فحصها لقاعدة {source_path.name}: {exc}") from exc

    if result is None or result[0] != "ok":
        raise BackupError(f"فشل PRAGMA quick_check للّقطة {source_path.name}: {result}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_snapshot(snapshot_dir: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, mode="w:gz", compresslevel=6) as archive:
        for snapshot in sorted(snapshot_dir.glob("*.db")):
            archive.add(snapshot, arcname=f"databases/{snapshot.name}", recursive=False)


def prune_local(output_dir: Path, label: str, retention_days: int) -> int:
    if retention_days < 1:
        raise BackupError("يجب أن تكون --retention-days أكبر من صفر.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for path in output_dir.glob(f"{label}-sqlite-*"):
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed


def build_backup(databases: list[Path], output_dir: Path, label: str) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{label}-sqlite-{timestamp}"
    archive_path = output_dir / f"{stem}.tar.gz"
    manifest_path = output_dir / f"{stem}.manifest.json"
    checksum_path = output_dir / f"{stem}.sha256"

    with tempfile.TemporaryDirectory(prefix="tozyw-sqlite-backup-") as temporary_directory:
        snapshot_dir = Path(temporary_directory)
        used_names: set[str] = set()
        for database in databases:
            snapshot_name = database.name
            if snapshot_name in used_names:
                raise BackupError(f"تكرار اسم قاعدة بيانات يمنع أرشفة آمنة: {snapshot_name}")
            used_names.add(snapshot_name)
            sqlite_snapshot(database, snapshot_dir / snapshot_name)

        archive_snapshot(snapshot_dir, archive_path)

    archive_hash = sha256(archive_path)
    manifest = {
        "format": 1,
        "created_at_utc": timestamp,
        "archive": archive_path.name,
        "sha256": archive_hash,
        "databases": [database.name for database in databases],
        "verification": "SQLite Backup API ثم PRAGMA quick_check",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_path.write_text(f"{archive_hash}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, manifest_path, checksum_path


def main() -> int:
    args = parse_args()
    databases = [path.expanduser().resolve() for path in args.database]
    output_dir = args.output_dir.expanduser().resolve()

    try:
        label = require_safe_label(args.label)
        with exclusive_lock(output_dir / ".sqlite-backup.lock"):
            archive_path, manifest_path, checksum_path = build_backup(databases, output_dir, label)
            removed = prune_local(output_dir, label, args.retention_days)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Backup created: {archive_path}")
    print(f"Manifest created: {manifest_path}")
    print(f"Checksum created: {checksum_path}")
    print(f"Pruned local files: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
