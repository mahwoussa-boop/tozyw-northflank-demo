"""اختبار توافق load_competitor_dfs مع قواعد المنافسين السابقة لترحيل v31.8."""
from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def _load_loader_under_test():
    """يترجم دالة التحميل عينها بلا استيراد اعتماديات واجهة التطبيق الثقيلة."""
    source_path = Path(__file__).parents[1] / "bootstrap.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "load_competitor_dfs"
    )
    isolated = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {"pd": pd, "__name__": "bootstrap_loader_test"}
    exec(compile(isolated, str(source_path), "exec"), namespace)
    return namespace["load_competitor_dfs"]


load_competitor_dfs = _load_loader_under_test()


class LegacyCompetitorSchemaTest(unittest.TestCase):
    def test_loader_returns_optional_columns_for_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE competitor_products_store (
                    id INTEGER PRIMARY KEY,
                    competitor TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    norm_name TEXT NOT NULL,
                    price REAL DEFAULT 0,
                    image_url TEXT DEFAULT '',
                    product_url TEXT DEFAULT '',
                    brand TEXT DEFAULT ''
                )"""
            )
            conn.execute(
                """INSERT INTO competitor_products_store
                   (id, competitor, product_name, norm_name, price, image_url, product_url, brand)
                   VALUES (1, 'متجر تجريبي', 'منتج تجريبي', 'منتج تجريبي', 99.0,
                           'https://example.test/image.jpg', 'https://example.test/item', 'علامة')"""
            )
            conn.commit()
            conn.close()

            groups = load_competitor_dfs(str(db_path))

        self.assertIn("متجر تجريبي", groups)
        frame = groups["متجر تجريبي"]
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, "agg_name"], "")
        self.assertEqual(frame.loc[0, "extracted_brand"], "")
        self.assertEqual(frame.loc[0, "extracted_size"], 0)
        self.assertEqual(frame.loc[0, "product_line"], "")
        self.assertEqual(frame.loc[0, "المنتج"], "منتج تجريبي")


if __name__ == "__main__":
    unittest.main()
