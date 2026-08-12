from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sqlite_backup.py"


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO products (name) VALUES (?)", ("عطر اختبار",))
        connection.commit()


class SQLiteBackupTests(unittest.TestCase):
    def test_creates_verified_archive_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            database = temp / "pricing.db"
            output_dir = temp / "backups"
            create_database(database)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--database",
                    str(database),
                    "--output-dir",
                    str(output_dir),
                    "--label",
                    "tozyw-test",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            archives = list(output_dir.glob("tozyw-test-sqlite-*.tar.gz"))
            manifests = list(output_dir.glob("tozyw-test-sqlite-*.manifest.json"))
            checksums = list(output_dir.glob("tozyw-test-sqlite-*.sha256"))
            self.assertEqual((len(archives), len(manifests), len(checksums)), (1, 1, 1))

            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["databases"], ["pricing.db"])
            self.assertEqual(manifest["verification"], "SQLite Backup API ثم PRAGMA quick_check")

            with tarfile.open(archives[0], "r:gz") as archive:
                self.assertEqual(archive.getnames(), ["databases/pricing.db"])
                archive.extractall(temp / "restored", filter="data")

            restored = temp / "restored" / "databases" / "pricing.db"
            with sqlite3.connect(f"file:{restored}?mode=ro", uri=True) as connection:
                self.assertEqual(connection.execute("SELECT name FROM products").fetchall(), [("عطر اختبار",)])

    def test_rejects_unsafe_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            database = temp / "pricing.db"
            create_database(database)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--database",
                    str(database),
                    "--output-dir",
                    str(temp / "backups"),
                    "--label",
                    "../unsafe",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--label", result.stderr)


if __name__ == "__main__":
    unittest.main()
