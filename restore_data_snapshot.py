from __future__ import annotations

import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


FILES = {
    "pricing_v18.db",
    "perfume_pricing.db",
    "pricing_cache.json",
    "missing_cache.json",
    "missing_products_queue.csv",
    "analysis_progress.json",
    "match_cache_v22.db",
}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: restore_data_snapshot.py DATA_DIR RESULTS_ARCHIVE_URL")
    data_dir = Path(sys.argv[1])
    url = sys.argv[2]
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "tozyw-results-complete.zip"
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            members = {Path(name).name: name for name in zf.namelist()}
            for filename in FILES:
                member = members.get(filename)
                if member is None:
                    continue
                target = data_dir / filename
                with zf.open(member) as src, target.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                print(f"restored {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
