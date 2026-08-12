from __future__ import annotations

import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: restore_data_snapshot.py DATA_DIR URL")
    data_dir = Path(sys.argv[1])
    url = sys.argv[2]
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "pricing_v18.db"
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "pricing_v18.db.zip"
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            member = next((n for n in zf.namelist() if n.endswith("pricing_v18.db")), None)
            if member is None:
                raise RuntimeError("pricing_v18.db was not found in the snapshot archive")
            with zf.open(member) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    print(f"restored {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
