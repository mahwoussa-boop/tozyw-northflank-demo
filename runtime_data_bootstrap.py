"""Initialize and version persistent data for the deployed Tozyw service.

A volume can outlive an image.  This module first ensures the bundled seed exists,
then optionally imports a specifically requested results archive into a staging
folder.  Every replacement is atomic and the prior UI snapshot is archived before
being rebuilt from the new caches.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
import zipfile
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
_IMPORT_MARKER = ".tozyw_results_revision"


def copy_atomically(source: Path, target: Path) -> None:
    """Copy a file via a sibling temporary file, then atomically replace target."""
    temporary = target.with_name(f".{target.name}.partial")
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        shutil.copystat(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def has_competitor_data(db_path: Path) -> bool:
    """Return whether a database holds at least one competitor product."""
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
    """Move incomplete seed-target files aside before one-time seed restoration."""
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


def _archive_members(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map basename to archive member, accepting Windows and POSIX separators."""
    members: dict[str, str] = {}
    for member in archive.namelist():
        normalized = member.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        if name:
            members[name] = member
    return members


def stage_remote_archive(url: str, stage_dir: Path) -> list[str]:
    """Download and extract the full results archive into a temporary staging folder."""
    archive_path = stage_dir / "results.zip"
    urllib.request.urlretrieve(url, archive_path)
    extracted: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        members = _archive_members(archive)
        for filename in SEED_FILES:
            member = members.get(filename)
            if member is None:
                continue
            target = stage_dir / filename
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            extracted.append(filename)
    return extracted


def archive_ui_snapshot(data_dir: Path, revision: str) -> Path | None:
    """Preserve the old UI snapshot before rendering a new snapshot from new caches."""
    snapshot_dir = data_dir / "ui_session"
    legacy_file = data_dir / "ui_session.json"
    if not snapshot_dir.exists() and not legacy_file.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = data_dir / "recovery_backups" / f"before-import-{revision}-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    if snapshot_dir.exists():
        snapshot_dir.replace(backup / "ui_session")
    if legacy_file.exists():
        legacy_file.replace(backup / "ui_session.json")
    return backup


def sync_runtime_views(data_dir: Path) -> None:
    """Generate all-competitor JSON and a UI snapshot from current persistent data."""
    from sync_competitor_list import sync_competitor_list
    from restore_ui_snapshot import restore_snapshot

    sync_info = sync_competitor_list(data_dir)
    snapshot_info = restore_snapshot(data_dir)
    print(f"synced competitor list: {sync_info}")
    print(f"restored UI snapshot: {snapshot_info}")


def import_requested_results(data_dir: Path) -> bool:
    """Import a requested archive revision only once, with validation before replacement."""
    url = os.environ.get("TOZYW_RESULTS_IMPORT_URL", "").strip()
    revision = os.environ.get("TOZYW_RESULTS_REVISION", "").strip()
    if not url or not revision:
        return False

    marker = data_dir / _IMPORT_MARKER
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == revision:
        return False

    with tempfile.TemporaryDirectory(prefix="tozyw-import-") as temp:
        stage_dir = Path(temp)
        extracted = stage_remote_archive(url, stage_dir)
        staged_db = stage_dir / "pricing_v18.db"
        if not has_competitor_data(staged_db):
            raise RuntimeError("requested results archive has no valid competitor database")
        required = {"pricing_v18.db", "pricing_cache.json", "missing_cache.json"}
        missing = sorted(required - set(extracted))
        if missing:
            raise RuntimeError(f"requested results archive is missing required files: {missing}")
        for filename in extracted:
            copy_atomically(stage_dir / filename, data_dir / filename)

    ui_backup = archive_ui_snapshot(data_dir, revision)
    sync_runtime_views(data_dir)
    temporary_marker = marker.with_name(f".{marker.name}.partial")
    temporary_marker.write_text(revision + "\n", encoding="utf-8")
    temporary_marker.replace(marker)
    print(
        f"imported requested results revision {revision}; "
        f"files={','.join(extracted)}; previous_ui_snapshot={ui_backup}"
    )
    return True


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/var/tozyw-demo/data"))
    seed_dir = Path(os.environ.get("TOZYW_SEED_DATA_DIR", "/var/tozyw-demo/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    competitor_db = data_dir / "pricing_v18.db"
    if not has_competitor_data(competitor_db):
        backup_dir = backup_incomplete_data(data_dir)
        copied = restore_seed(seed_dir, data_dir)
        if not has_competitor_data(competitor_db):
            raise RuntimeError(
                "the bundled snapshot did not contain a valid competitor database; "
                f"check {seed_dir}"
            )
        backup_note = f"; backed up incomplete files to {backup_dir}" if backup_dir else ""
        print("restored persistent data snapshot: " + ", ".join(copied) + backup_note)

    imported = import_requested_results(data_dir)
    if not imported:
        sync_runtime_views(data_dir)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # Startup must still surface the actual issue.
        print(f"persistent data bootstrap failed: {exc}", file=sys.stderr)
        raise
