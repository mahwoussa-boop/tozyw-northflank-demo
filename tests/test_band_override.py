# -*- coding: utf-8 -*-
"""A band override needs a reason, an audit event, and no control fields in Make."""
import sqlite3

from services.send_band_guard import (
    record_and_strip_band_overrides,
    split_outside_band,
)
from utils.db_manager import _normalize_for_store


def _market_db(path, name: str) -> str:
    db = str(path)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE opportunity_scores "
        "(norm_name TEXT, price_median REAL, stale_reason TEXT)"
    )
    conn.execute(
        "INSERT INTO opportunity_scores VALUES (?, ?, NULL)",
        (_normalize_for_store(name), 100.0),
    )
    conn.commit()
    conn.close()
    return db


def test_override_with_reason_passes_guard_and_is_audited(tmp_path, monkeypatch):
    name = "Perfume Outlier Alpha 100ml"
    db = _market_db(tmp_path / "market.db", name)
    allowed, held = split_outside_band(
        [{"name": name, "NO": "77", "price": 130.0, "section": "raise",
          "_band_override_reason": "official competitor campaign verified"}],
        db_path=db,
    )
    assert held == [] and len(allowed) == 1

    calls = []
    import services.decision_journal as journal
    monkeypatch.setattr(journal, "record_decision", lambda *args, **kwargs: calls.append((args, kwargs)))
    cleaned = record_and_strip_band_overrides(allowed)

    assert cleaned == [{"name": name, "NO": "77", "price": 130.0, "section": "raise"}]
    assert calls[0][0][:3] == ("band_override", name, "raise")
    assert calls[0][1]["extra"]["reason"] == "official competitor campaign verified"


def test_empty_override_reason_does_not_bypass_guard(tmp_path):
    name = "Perfume Guarded Alpha 100ml"
    db = _market_db(tmp_path / "market.db", name)
    allowed, held = split_outside_band(
        [{"name": name, "price": 130.0, "section": "raise", "_band_override_reason": "  "}],
        db_path=db,
    )
    assert allowed == [] and len(held) == 1
