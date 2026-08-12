"""tests/test_populate_our_catalog.py — ملء our_catalog يُصلح إشعار «ليس لديك» الكاذب.

الجذر: upsert_our_catalog() لم تكن تُستدعى ⇒ our_catalog فارغ ⇒ مطابق الملكية
بلا مرجع ⇒ كل منتج منافس «ليس لديك» (missing) حتى المملوك. الإصلاح يملأ الجدول
من الكتالوج المرفوع، فيظهر المملوك «لديك» (owned). لا يُغيّر منطق المطابقة.
"""
import pandas as pd
import pytest

import utils.db_manager as dbm
from conf.constants import COL_OUR_ID, COL_OUR_NAME, COL_OUR_PRICE
from services.matching_service import Ownership
from services.ownership_service import (
    KEY_OWNERSHIP,
    build_ownership_matcher,
    enrich_row_ownership,
)

_OWNED_NAME = "عطر ديور سوفاج 100مل"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """DB مؤقتة بجدول our_catalog فارغ (كل استدعاءات get_db تراها عبر DB_PATH)."""
    monkeypatch.setattr(dbm, "DB_PATH", str(tmp_path / "t.db"))
    dbm.init_db_v26()  # ينشئ our_catalog في القاعدة المؤقتة
    return tmp_path


def _catalog_df():
    """كتالوج بأعمدة داخلية واقعية (ناتج load_catalog بعد map_columns)."""
    return pd.DataFrame({
        COL_OUR_NAME: [_OWNED_NAME, "عطر شانيل نمبر 5 عيار 100"],
        COL_OUR_ID: ["1001", "1002"],
        COL_OUR_PRICE: ["450", "600"],
    })


def test_upsert_populates_and_get_our_catalog_returns(fresh_db):
    res = dbm.upsert_our_catalog(
        _catalog_df(), name_col=COL_OUR_NAME, id_col=COL_OUR_ID, price_col=COL_OUR_PRICE,
    )
    assert res["inserted"] == 2
    rows = dbm.get_our_catalog()
    names = {r["product_name"] for r in rows}
    assert _OWNED_NAME in names
    assert len(rows) == 2


def test_owned_product_shows_owned_after_populate(fresh_db):
    dbm.upsert_our_catalog(
        _catalog_df(), name_col=COL_OUR_NAME, id_col=COL_OUR_ID, price_col=COL_OUR_PRICE,
    )
    matching, name_to_id = build_ownership_matcher()
    comp_row = {"product_name": _OWNED_NAME, "brand": "Dior"}
    enriched = enrich_row_ownership(comp_row, matching, name_to_id)
    assert enriched[KEY_OWNERSHIP] == Ownership.OWNED.value  # «لديك» لا «ليس لديك»


def test_empty_catalog_reproduces_false_missing(fresh_db):
    # بلا ملء ⇒ الكتالوج فارغ ⇒ المنتج المملوك يظهر «ليس لديك» (البُقّ الأصلي).
    matching, name_to_id = build_ownership_matcher()
    enriched = enrich_row_ownership({"product_name": _OWNED_NAME}, matching, name_to_id)
    assert enriched[KEY_OWNERSHIP] == Ownership.MISSING.value


def test_upsert_skips_rows_without_name(fresh_db):
    df = pd.DataFrame({
        COL_OUR_NAME: [_OWNED_NAME, "", "   "],
        COL_OUR_ID: ["1001", "1002", "1003"],
        COL_OUR_PRICE: ["450", "10", "20"],
    })
    res = dbm.upsert_our_catalog(df, name_col=COL_OUR_NAME, id_col=COL_OUR_ID, price_col=COL_OUR_PRICE)
    assert res["inserted"] == 1  # الصفوف بلا اسم تُتخطّى
    assert len(dbm.get_our_catalog()) == 1


def test_reupsert_updates_not_duplicates(fresh_db):
    df = _catalog_df()
    dbm.upsert_our_catalog(df, name_col=COL_OUR_NAME, id_col=COL_OUR_ID, price_col=COL_OUR_PRICE)
    res2 = dbm.upsert_our_catalog(df, name_col=COL_OUR_NAME, id_col=COL_OUR_ID, price_col=COL_OUR_PRICE)
    assert res2["inserted"] == 0
    assert res2["updated"] == 2
    assert len(dbm.get_our_catalog()) == 2  # لا تكرار
