#!/usr/bin/env python3
"""تحقق غير مدمر من أرشيف نسخة Tozyw المستعاد محلياً.

يفك هذا البرنامج أرشيفاً محدداً إلى مجلد جديد فقط، ويتحقق من SHA-256 وSQLite.
لا يكتب أبداً إلى DATA_DIR أو إلى أي قاعدة إنتاج.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tarfile
import tempfile


class RestoreVerificationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a restored Tozyw SQLite backup archive.")
    parser.add_argument("--archive", required=True, type=Path, help="أرشيف tar.gz المستعاد من وجهة crypt.")
    parser.add_argument("--manifest", required=True, type=Path, help="ملف manifest.json المرافق للأرشيف.")
    parser.add_argument("--output-dir", required=True, type=Path, help="مجلد جديد لمسار التحقق؛ يرفض إن كان غير فارغ.")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members:
        raise RestoreVerificationError("الأرشيف فارغ.")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise RestoreVerificationError(f"عضو أرشيف غير آمن: {member.name}")
        if not member.isfile():
            raise RestoreVerificationError(f"نوع عضو غير متوقع: {member.name}")
        if not member.name.startswith("databases/") or not member.name.endswith(".db"):
            raise RestoreVerificationError(f"مسار قاعدة غير متوقع: {member.name}")
    return members


def verify_database(database: Path) -> tuple[str, int]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        table_count = connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    if quick_check is None or quick_check[0] != "ok":
        raise RestoreVerificationError(f"فشل PRAGMA quick_check للقاعدة {database.name}: {quick_check}")
    return database.name, table_count


def main() -> int:
    args = parse_args()
    archive_path = args.archive.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    try:
        if not archive_path.is_file() or not manifest_path.is_file():
            raise RestoreVerificationError("الأرشيف أو ملف manifest غير موجود.")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise RestoreVerificationError("مجلد التحقق موجود وغير فارغ؛ استخدم مجلداً جديداً.")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_hash = manifest.get("sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise RestoreVerificationError("manifest لا يحوي SHA-256 صالحاً.")
        actual_hash = sha256(archive_path)
        if actual_hash != expected_hash:
            raise RestoreVerificationError("بصمة SHA-256 لا تطابق manifest؛ أوقف الاستعادة.")

        output_dir.mkdir(parents=True, exist_ok=False)
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = ensure_safe_members(archive)
            archive.extractall(output_dir, members=members, filter="data")

        databases = sorted((output_dir / "databases").glob("*.db"))
        expected_databases = sorted(manifest.get("databases", []))
        if [database.name for database in databases] != expected_databases:
            raise RestoreVerificationError("قواعد الأرشيف لا تطابق أسماء قواعد manifest.")

        verified = [verify_database(database) for database in databases]
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError, sqlite3.Error, RestoreVerificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Archive verified: {archive_path.name}")
    for database_name, table_count in verified:
        print(f"SQLite verified: {database_name} ({table_count} tables)")
    print(f"Restored safely into: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
