# -*- coding: utf-8 -*-
"""The 250k missing-products cap must not depend on SQLite insertion order."""
import sqlite3

import pandas as pd

from engines.competitor_intelligence import CompetitorIntelligence


def _seed(path, products):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE competitor_products_store (
            competitor TEXT NOT NULL, product_name TEXT NOT NULL, brand TEXT,
            category TEXT, image_url TEXT, image_urls TEXT, price REAL,
            added_at TEXT, first_seen_at TEXT
        )"""
    )
    conn.executemany(
        """INSERT INTO competitor_products_store
           (competitor, product_name, brand, category, image_url, image_urls,
            price, added_at, first_seen_at)
           VALUES (?, ?, ?, '', '', '', ?, '2026-01-01', ?)""",
        products,
    )
    conn.commit()
    conn.close()


def _selected_names(path, monkeypatch):
    monkeypatch.setenv("MISSING_MAX_UNIQUE", "2")
    our_catalog = pd.DataFrame({"product_name": ["Existing catalogue product"]})
    engine = CompetitorIntelligence(db_path=str(path))
    rows, total = engine.find_missing_products(our_catalog, per_page=10)
    assert total == 2 and engine.last_capped is True
    return [row["product_name"] for row in rows], engine.last_cap_skipped_rows


def test_cap_is_deterministic_and_keeps_highest_price_items(tmp_path, monkeypatch):
    products = [
        ("Store B", "Perfume Low Alpha 100ml", "Brand", 100.0, "2026-01-01"),
        ("Store A", "Perfume High Alpha 100ml", "Brand", 900.0, "2026-01-03"),
        ("Store C", "Perfume Mid Alpha 100ml", "Brand", 500.0, "2026-01-02"),
    ]
    first = tmp_path / "first.db"
    reordered = tmp_path / "reordered.db"
    _seed(first, products)
    _seed(reordered, list(reversed(products)))

    names1, skipped1 = _selected_names(first, monkeypatch)
    names2, skipped2 = _selected_names(reordered, monkeypatch)

    assert names1 == names2 == [
        "Perfume High Alpha 100ml", "Perfume Mid Alpha 100ml",
    ]
    assert skipped1 == skipped2 == 1
