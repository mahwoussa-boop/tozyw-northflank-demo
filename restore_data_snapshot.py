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

    # تحضير العروض المشتقة مرة واحدة أثناء البناء (16GB)، لا عند أول تشغيل
    # للخدمة ذات الذاكرة الأصغر. فشل هنا أفضل من نشر صورة بلا لقطة واجهة.
    from restore_ui_snapshot import restore_snapshot
    from sync_competitor_list import sync_competitor_list

    competitor_info = sync_competitor_list(data_dir)
    snapshot_info = restore_snapshot(data_dir)
    print(f"seed competitor list: {competitor_info}")
    print(f"seed UI snapshot: {snapshot_info}")


if __name__ == "__main__":
    main()
