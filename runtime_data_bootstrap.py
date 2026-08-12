"""Initialise a persistent runtime data directory without overwriting valid data.

The Docker image holds a read-only seed snapshot under ``TOZYW_SEED_DATA_DIR``.
On a first Northflank volume mount, this script copies the snapshot to ``DATA_DIR``.
A SQLite file alone is not considered valid: it must contain the competitor table with
at least one record. This prevents a newly-created empty SQLite database from hiding
the shipped historical results after a volume is attached.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SEED_FILES = (
    "pricing_v18.db",
    "perfume_pricing.db",
    "pricing_cache.json",
    "missing_cache.json",
    "missing_products_queue.csv",
    "analysis_progress.json",
    "match_cache_v22.db",
)


def copy_atomically(source: Path, target: Path) -> None:
    """Copy *source* to *target* atomically, preserving a valid SQLite file."""
    temporary = target.with_name(f".{target.name}.partial")
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        shutil.copystat(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def has_competitor_data(db_path: Path) -> bool:
    """Return whether *db_path* contains a non-empty competitor store."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='competitor_products_store'"
            ).fetchone()
            if not table_exists:
                return False
            return connection.execute(
                "SELECT 1 FROM competitor_products_store LIMIT 1"
            ).fetchone() is not None
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def backup_incomplete_data(data_dir: Path) -> Path | None:
    """Move incomplete seed-target files aside before a one-time restoration."""
    present = [data_dir / name for name in SEED_FILES if (data_dir / name).exists()]
    if not present:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = data_dir / "recovery_backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for source in present:
        source.replace(backup_dir / source.name)
    return backup_dir


def restore_seed(seed_dir: Path, data_dir: Path) -> list[str]:
    """Restore all available seed files and return copied file descriptions."""
    copied: list[str] = []
    for filename in SEED_FILES:
        source = seed_dir / filename
        target = data_dir / filename
        if source.exists() and source.is_file():
            copy_atomically(source, target)
            copied.append(f"{filename} ({target.stat().st_size} bytes)")
    return copied


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/var/tozyw-demo/data"))
    seed_dir = Path(os.environ.get("TOZYW_SEED_DATA_DIR", "/var/tozyw-demo/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    competitor_db = data_dir / "pricing_v18.db"
    if has_competitor_data(competitor_db):
        print(f"valid persistent competitor data already present: {competitor_db}")
        return

    backup_dir = backup_incomplete_data(data_dir)
    copied = restore_seed(seed_dir, data_dir)
    if not has_competitor_data(competitor_db):
        raise RuntimeError(
            "the bundled snapshot did not contain a valid competitor database; "
            f"check {seed_dir}"
        )

    backup_note = f"; backed up incomplete files to {backup_dir}" if backup_dir else ""
    print("restored persistent data snapshot: " + ", ".join(copied) + backup_note)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # Startup must still surface the actual issue.
        print(f"persistent data bootstrap failed: {exc}", file=sys.stderr)
        raise
