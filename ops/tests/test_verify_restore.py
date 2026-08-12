from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_restore.py"


def build_archive(directory: Path, malicious: bool = False) -> tuple[Path, Path]:
    database = directory / "pricing.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE catalog (sku TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO catalog VALUES ('SKU-1')")
        connection.commit()

    archive = directory / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(database, arcname="../escape.db" if malicious else "databases/pricing.db")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = directory / "backup.manifest.json"
    manifest.write_text(
        json.dumps({"sha256": digest, "databases": ["pricing.db"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return archive, manifest


class RestoreVerificationTests(unittest.TestCase):
    def test_verifies_and_extracts_valid_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive, manifest = build_archive(root)
            output = root / "verification"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--archive",
                    str(archive),
                    "--manifest",
                    str(manifest),
                    "--output-dir",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SQLite verified: pricing.db", result.stdout)
            self.assertTrue((output / "databases" / "pricing.db").is_file())

    def test_rejects_path_traversal_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive, manifest = build_archive(root, malicious=True)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--archive",
                    str(archive),
                    "--manifest",
                    str(manifest),
                    "--output-dir",
                    str(root / "verification"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("غير آمن", result.stderr)


if __name__ == "__main__":
    unittest.main()
