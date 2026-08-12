"""Initialize and version persistent data for the deployed Tozyw service.

A volume outlives an image, so the volume — not the image — is where results live.
This module imports the requested results archive into a staging folder **inside**
``DATA_DIR``, validates it, and only then moves it into place.  Every replacement is
atomic and the prior UI snapshot is archived before being rebuilt from the new caches.

Order matters: the requested import runs *before* any bundled-seed fallback.  The
previous order checked the seed first and raised when it was absent, which made a
slim image on an empty volume impossible to boot — the import was never reached.
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
_SEED_RUNTIME_FILES = ("competitors_list_v30.json",)
_SNAPSHOT_META = Path("ui_session") / "_meta.json"
_IMPORT_MARKER = ".tozyw_results_revision"
_STAGING_DIR = ".staging"
DEFAULT_DATA_DIR = "/data"


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


def move_into_place(source: Path, target: Path) -> None:
    """Move a staged file onto its target, falling back to a copy across devices.

    Staging now lives inside ``DATA_DIR``, so this is a same-filesystem rename:
    atomic and instant, instead of copying ~910MB a second time.
    """
    try:
        source.replace(target)
    except OSError:
        copy_atomically(source, target)


def assert_writable(data_dir: Path) -> None:
    """Fail early, and in plain words, when the mounted volume is not writable."""
    probe = data_dir / ".tozyw_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"مجلد البيانات غير قابل للكتابة: {data_dir} ({exc}). "
            "تأكّد أن الـVolume مثبَّت على هذا المسار وأن ملكيته لمستخدم الصورة "
            "(USER في الـDockerfile)."
        ) from exc
    finally:
        probe.unlink(missing_ok=True)


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
    """Move the current data files aside, into ``recovery_backups/<timestamp>/``.

    Used before a seed restore *and* before an import replaces a full data set.
    These are same-filesystem renames, so preserving ~1GB of previous results
    costs no copy time — only the space, which is deliberate: nothing is deleted.
    """
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


def restore_seed_runtime_views(seed_dir: Path, data_dir: Path) -> list[str]:
    """Copy derived UI assets prepared during image build when the volume lacks them.

    This keeps normal pod startup within the service memory limit. The expensive
    DataFrame reconstruction remains available only for an explicit new archive
    import, where a revision marker guarantees that it runs once.
    """
    copied: list[str] = []
    for filename in _SEED_RUNTIME_FILES:
        source = seed_dir / filename
        target = data_dir / filename
        if source.is_file() and not target.exists():
            copy_atomically(source, target)
            copied.append(filename)

    source_snapshot = seed_dir / "ui_session"
    target_snapshot = data_dir / "ui_session"
    if source_snapshot.is_dir() and not target_snapshot.exists():
        temporary = data_dir / ".ui_session.partial"
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            shutil.copytree(source_snapshot, temporary)
            temporary.replace(target_snapshot)
            copied.append("ui_session/")
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    return copied


def runtime_views_ready(data_dir: Path) -> bool:
    """Return whether the persistent volume already has both derived UI assets."""
    return (
        (data_dir / "competitors_list_v30.json").is_file()
        and (data_dir / _SNAPSHOT_META).is_file()
    )


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
    try:
        urllib.request.urlretrieve(url, archive_path)
    except OSError as exc:  # urllib raises HTTPError/URLError, both OSError subclasses.
        raise RuntimeError(
            f"تعذّر تنزيل أرشيف النتائج من {url}: {exc}. "
            "تحقّق أن الإصدار **منشور** لا مسودة — رابط المسودة (untagged-…) يُرجع 404."
        ) from exc
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

    # Staging lives on the volume, not /tmp: the archive needs ~1.3GB unpacked and
    # the container's ephemeral disk is the wrong place to bet that on.
    staging_root = data_dir / _STAGING_DIR
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-", dir=staging_root) as temp:
        stage_dir = Path(temp)
        extracted = stage_remote_archive(url, stage_dir)
        staged_db = stage_dir / "pricing_v18.db"
        if not has_competitor_data(staged_db):
            raise RuntimeError("requested results archive has no valid competitor database")
        required = {"pricing_v18.db", "pricing_cache.json", "missing_cache.json"}
        missing = sorted(required - set(extracted))
        if missing:
            raise RuntimeError(f"requested results archive is missing required files: {missing}")
        # Only once the staged set is proven valid do we touch the live files.
        data_backup = backup_incomplete_data(data_dir)
        for filename in extracted:
            move_into_place(stage_dir / filename, data_dir / filename)

    ui_backup = archive_ui_snapshot(data_dir, revision)
    sync_runtime_views(data_dir)
    temporary_marker = marker.with_name(f".{marker.name}.partial")
    temporary_marker.write_text(revision + "\n", encoding="utf-8")
    temporary_marker.replace(marker)
    print(
        f"imported requested results revision {revision}; "
        f"files={','.join(extracted)}; previous_data={data_backup}; "
        f"previous_ui_snapshot={ui_backup}"
    )
    return True


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR))
    data_dir.mkdir(parents=True, exist_ok=True)
    assert_writable(data_dir)

    competitor_db = data_dir / "pricing_v18.db"
    had_usable_data = has_competitor_data(competitor_db)

    # 1) The explicitly requested import runs first.  On an empty volume there is no
    #    seed in the image, and checking the seed first used to raise before the
    #    import was ever attempted.  ``import_requested_results`` already rebuilds
    #    the derived views, so a successful import ends the bootstrap.
    try:
        if import_requested_results(data_dir):
            return
    except Exception as exc:
        if not had_usable_data:
            raise
        # A bad import URL must not take down a service whose data is already good;
        # serving the previous results beats serving an empty dashboard.
        print(
            f"فشل استيراد النتائج المطلوبة، والخدمة تُكمل بالبيانات الموجودة: {exc}",
            file=sys.stderr,
        )

    if not has_competitor_data(competitor_db):
        # 2) Optional image-bundled seed — unused by the Northflank deployment,
        #    kept for local runs and for platforms without a results URL.
        seed_dir = Path(os.environ.get("TOZYW_SEED_DATA_DIR", "").strip() or data_dir)
        if seed_dir != data_dir and seed_dir.is_dir():
            backup_dir = backup_incomplete_data(data_dir)
            copied = restore_seed(seed_dir, data_dir)
            backup_note = f"; backed up incomplete files to {backup_dir}" if backup_dir else ""
            print("restored persistent data snapshot: " + ", ".join(copied) + backup_note)
        if not has_competitor_data(competitor_db):
            raise RuntimeError(
                f"لا توجد قاعدة منافسين صالحة في {data_dir}. "
                "اضبط TOZYW_RESULTS_IMPORT_URL وTOZYW_RESULTS_REVISION لاستيراد أرشيف "
                "النتائج، أو وفّر بذرة مرفقة عبر TOZYW_SEED_DATA_DIR."
            )

    # The requested import path already rebuilds derived views atomically. For a
    # warm volume, reuse a valid snapshot; otherwise build it once from the data.
    if runtime_views_ready(data_dir):
        print("reused prepared runtime views from persistent data")
        return
    sync_runtime_views(data_dir)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # Startup must still surface the actual issue.
        print(f"persistent data bootstrap failed: {exc}", file=sys.stderr)
        raise
