from __future__ import annotations

import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def restore_one(data_dir: Path, filename: str, url: str) -> None:
    target = data_dir / filename
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / f"{filename}.zip"
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            member = next((n for n in zf.namelist() if n.endswith(filename)), None)
            if member is None:
                raise RuntimeError(f"{filename} was not found in the snapshot archive")
            with zf.open(member) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    print(f"restored {target} ({target.stat().st_size} bytes)")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: restore_data_snapshot.py DATA_DIR PRICING_URL PERFUME_URL")
    data_dir = Path(sys.argv[1])
    data_dir.mkdir(parents=True, exist_ok=True)
    restore_one(data_dir, "pricing_v18.db", sys.argv[2])
    restore_one(data_dir, "perfume_pricing.db", sys.argv[3])


if __name__ == "__main__":
    main()
