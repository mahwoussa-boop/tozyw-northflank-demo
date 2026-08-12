"""Initialise a persistent runtime data directory without overwriting existing data.

The Docker image contains a read-only seed snapshot under ``TOZYW_SEED_DATA_DIR``.
When a Northflank volume is mounted at ``DATA_DIR`` for the first time, this script
copies the seed files into that empty volume. Later restarts leave the volume
untouched, so SQLite results persist across deploys.
"""
from __future__ import annotations

import os
import shutil
import sys
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


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/var/tozyw-demo/data"))
    seed_dir = Path(os.environ.get("TOZYW_SEED_DATA_DIR", "/var/tozyw-demo/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    # A valid competitor database is the durable source of truth. Never replace
    # it when it already exists, even if other optional cache files are absent.
    competitor_db = data_dir / "pricing_v18.db"
    if competitor_db.exists() and competitor_db.stat().st_size > 0:
        print(f"persistent data already present: {competitor_db}")
        return

    copied: list[str] = []
    for filename in SEED_FILES:
        source = seed_dir / filename
        target = data_dir / filename
        if source.exists() and source.is_file() and not target.exists():
            copy_atomically(source, target)
            copied.append(f"{filename} ({target.stat().st_size} bytes)")

    if copied:
        print("initialised persistent data directory: " + ", ".join(copied))
    else:
        print(
            "no bundled data snapshot found; continuing with an empty persistent "
            f"directory at {data_dir}"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # Startup must still surface the actual issue.
        print(f"persistent data bootstrap failed: {exc}", file=sys.stderr)
        raise
