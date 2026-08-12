"""tests/test_quantity_payload.py — حمولة تحديث الكمية (Salla UpdateProduct: الكمية فقط).

تتحقّق من إعادة استخدام هوية تحديث السعر (NO/product_id) دون لمس السعر، وتصفير
الكمية (0 ⇒ نفذ لدينا)، والحُرّاس (لا اسم ⇒ يُتخطّى).
"""
import pandas as pd

from services.export_service import ExportService, to_quantity_payload


def _row(**kw):
    base = {"No.": "12345", "أسم المنتج": "عطر تجريبي", "السعر": 100}
    base.update(kw)
    return base


def test_builds_quantity_item_with_identity() -> None:
    items = to_quantity_payload(pd.DataFrame([_row()]), 0)
    assert len(items) == 1
    it = items[0]
    assert it["NO"] == "12345"
    assert it["product_id"] == "12345"
    assert it["name"] == "عطر تجريبي"
    assert it["quantity"] == 0
    assert it["section"] == "quantity"
    assert "price" not in it  # تحديث الكمية لا يمسّ السعر


def test_nonzero_quantity() -> None:
    items = to_quantity_payload(pd.DataFrame([_row()]), 7)
    assert items[0]["quantity"] == 7


def test_negative_quantity_clamped_to_zero() -> None:
    items = to_quantity_payload(pd.DataFrame([_row()]), -5)
    assert items[0]["quantity"] == 0


def test_empty_df_and_none_return_empty() -> None:
    assert to_quantity_payload(pd.DataFrame(), 0) == []
    assert to_quantity_payload(None, 0) == []


def test_missing_name_is_skipped() -> None:
    df = pd.DataFrame([{"No.": "9", "السعر": 50}])  # لا اسم ⇒ يُتخطّى
    assert to_quantity_payload(df, 0) == []


def test_no_falls_back_to_product_id() -> None:
    # لا «No.» لكن يوجد معرف_المنتج ⇒ NO يرتدّ إليه (نفس منطق تحديث السعر)
    df = pd.DataFrame([{"معرف_المنتج": "777", "أسم المنتج": "عطر", "السعر": 10}])
    items = to_quantity_payload(df, 3)
    assert items[0]["NO"] == "777"
    assert items[0]["product_id"] == "777"


def test_service_method_matches_function() -> None:
    df = pd.DataFrame([_row()])
    assert ExportService().quantity_payload(df, 2) == to_quantity_payload(df, 2)


def test_multiple_rows() -> None:
    df = pd.DataFrame([_row(), _row(**{"No.": "999", "أسم المنتج": "عطر ٢"})])
    items = to_quantity_payload(df, 0)
    assert [it["NO"] for it in items] == ["12345", "999"]
    assert all(it["quantity"] == 0 for it in items)
