"""tests/test_export_service.py — اختبارات التصدير (P3)."""
import pandas as pd
import pytest

import utils.send_log as _sl
from services.export_service import (
    SALLA_SHAMEL_COLUMNS,
    ExportService,
    _clean_pid,
    _extract_no,
    _section_price,
    confirmed_new_catalog_row,
    to_make_payload,
    to_new_products_payload,
)


@pytest.fixture(autouse=True)
def _isolate_send_log(monkeypatch, tmp_path):
    """يعزل ``send_log`` إلى قاعدة مؤقتة لكل اختبار في هذا الملف.

    اختبارات ``post_to_make`` تستدعي ``log_send`` (عبر ``_log_send_batch``)، وكانت
    تكتب في ``pricing_v18.db`` الحيّ لأنها لم تُوجّه القاعدة — فلوّثت ``send_log``
    بصفوف وهمية («س»/400، «دفعة تحديث» 200/202) مع **كل تشغيل للبوّابة**. هذا العزل
    يُنهي التلوّث (نفس نمط ``tests/test_send_log_export.py``). دفاعيّ لأي اختبار لاحق."""
    db = str(tmp_path / "sendlog.db")
    monkeypatch.setattr(_sl, "get_data_db_path", lambda *_a, **_k: db)
    _sl.init_send_log(db)
    # تأكيد Make في هذه الوحدة لا يجوز أن يكتب في our_catalog الحيّ؛ الاختبار
    # المتخصص أدناه يحقن مزامنته الخاصة ويتحقق من نداءها.
    monkeypatch.setattr(
        "services.export_service.sync_confirmed_new_product",
        lambda *_args: {"synced": False, "catalog_row": None},
    )


def test_salla_columns_exact_count_and_first() -> None:
    assert len(SALLA_SHAMEL_COLUMNS) == 40
    assert SALLA_SHAMEL_COLUMNS[0] == "النوع "       # مسافة لاحقة محفوظة
    assert SALLA_SHAMEL_COLUMNS[-1] == "[3] الصورة / اللون"


def test_clean_pid() -> None:
    assert _clean_pid("100.0") == "100"
    assert _clean_pid("0") == "" and _clean_pid("nan") == "" and _clean_pid(None) == ""
    assert _clean_pid("ABC") == "ABC"


def test_extract_no_reads_aliases() -> None:
    assert _extract_no({"No.": "55.0"}) == "55"
    assert _extract_no({"NO": 77}) == "77"
    assert _extract_no({"رقم المنتج": "9"}) == "9"


def test_section_price_rules() -> None:
    assert _section_price("raise", 50, 100) == 99.0      # comp-1
    assert _section_price("lower", 200, 100) == 99.0     # comp-1
    assert _section_price("approved", 50, 100) == 50     # سعرنا
    assert _section_price("missing", 50, 100) == 100     # سعر المنافس


def test_make_payload_structure_and_context() -> None:
    df = pd.DataFrame([{
        "معرف_المنتج": "100.0", "المنتج": "عطر تجريبي", "السعر": 80,
        "سعر_المنافس": 100, "المنافس": "متجر س", "الفرق": -20,
        "نسبة_التطابق": 95, "القرار": "🟢 سعر أقل", "الماركة": "Chanel",
    }])
    payload = to_make_payload(df, section_type="lower")
    assert len(payload) == 1
    p = payload[0]
    assert p["product_id"] == "100" and p["name"] == "عطر تجريبي"
    assert p["price"] == 99.0 and p["section"] == "lower"
    assert "comp_name" not in p  # لا عمود منتج_المنافس ⇒ يُحذف الحقل
    assert p["competitor"] == "متجر س" and p["brand"] == "Chanel"
    assert p["match_score"] == 95 and p["decision"] == "🟢 سعر أقل"


def test_make_payload_skips_nameless_rows() -> None:
    df = pd.DataFrame([{"معرف_المنتج": "1", "المنتج": ""}])
    assert to_make_payload(df) == []


def test_post_to_make_with_injected_poster() -> None:
    class _Resp:
        status_code = 200

    captured = {}

    def poster(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp()

    svc = ExportService(poster=poster)
    result = svc.post_to_make("http://hook", [{"NO": "1"}])
    assert result["success"] is True and result["status_code"] == 200
    assert captured["payload"] == {"products": [{"NO": "1"}]}


def test_new_products_payload_salla_arabic_keys() -> None:
    # المنتج المفقود ⇒ مفاتيح سلة العربية + السعر = منافس−1 + بلا NO (لا Salla ID بعدُ).
    df = pd.DataFrame([{
        "منتج_المنافس": "عطر مفقود", "سعر_المنافس": 250, "الماركة": "Dior",
        "صورة_المنافس": "https://img/x.jpg", "الوصف": "وصف",
    }])
    items = to_new_products_payload(df)
    assert len(items) == 1
    it = items[0]
    assert it["أسم المنتج"] == "عطر مفقود"
    assert it["سعر المنتج"] == 249           # منافس − 1
    assert it["صورة المنتج"] == "https://img/x.jpg"
    assert "NO" not in it                     # منتج جديد بلا Salla ID ⇒ يُحذف الفارغ
    assert it["الوزن"] == 1
    assert it["الماركة"] == "Dior"            # الماركة نصّاً


def test_new_products_payload_category_uses_contract_key() -> None:
    # التصنيف يُرسَل بمفتاح العقد «اسم التصنيف» (الذي يربطه Make)، لا «تصنيف المنتج» وحده.
    df = pd.DataFrame([{
        "منتج_المنافس": "عطر مفقود", "سعر_المنافس": 250, "الماركة": "Dior",
        "تصنيف_المنتج": "عطور رجالية", "الوصف": "وصف",
    }])
    it = to_new_products_payload(df)[0]
    assert it["اسم التصنيف"] == "عطور رجالية"   # ← مفتاح العقد (كان مفقوداً)
    assert it["الماركة"] == "Dior"


def test_post_to_make_data_envelope_and_real_error_body() -> None:
    # المنتجات الجديدة ⇒ مظروف «data»؛ والفشل يكشف نص رد Make الحقيقي (response.text).
    class _Resp:
        status_code = 400
        text = "Invalid product: missing name"

    captured = {}

    def poster(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return _Resp()

    svc = ExportService(poster=poster)
    result = svc.post_to_make("http://hook", [{"أسم المنتج": "س"}], envelope="data")
    assert captured["payload"] == {"data": [{"أسم المنتج": "س"}]}
    assert result["success"] is False
    assert result["body"] == "Invalid product: missing name"
    assert "Invalid product" in result["error"]


def test_post_to_make_202_is_accepted_not_confirmed() -> None:
    class _Resp:
        status_code = 202
        text = "Accepted"

    svc = ExportService(poster=lambda *a, **k: _Resp())
    result = svc.post_to_make("http://hook", [{"NO": "1"}])
    assert result["success"] is True
    assert result["state"] == "accepted"
    assert result["confirmed"] is False


def test_new_product_requires_salla_product_id_before_confirmation() -> None:
    class _Resp:
        status_code = 200
        text = '{"ok": true}'

    svc = ExportService(poster=lambda *a, **k: _Resp())
    result = svc.post_to_make("http://hook", [{"اسم المنتج": "عطر"}], envelope="data")
    assert result["success"] is True
    assert result["state"] == "accepted"
    assert result["confirmed"] is False


def test_new_product_with_salla_product_id_is_confirmed() -> None:
    class _Resp:
        status_code = 200
        text = '{"ok": true, "product_id": 12345}'

    svc = ExportService(poster=lambda *a, **k: _Resp())
    result = svc.post_to_make("http://hook", [{"اسم المنتج": "عطر"}], envelope="data")
    assert result["success"] is True
    assert result["state"] == "confirmed"
    assert result["confirmed"] is True and result["product_id"] == 12345


def test_confirmed_new_product_catalog_row_uses_returned_salla_id() -> None:
    row = confirmed_new_catalog_row(
        {"أسم المنتج": "عطر مؤكد", "سعر المنتج": 249, "product_id": "منافس-9"},
        12345,
    )
    assert row == {"product_id": "12345", "product_name": "عطر مؤكد", "price": 249.0}


def test_confirmed_single_new_product_syncs_local_catalog(monkeypatch) -> None:
    class _Resp:
        status_code = 200
        text = '{"ok": true, "product_id": 12345}'

    captured = {}
    monkeypatch.setattr(
        "services.export_service.sync_confirmed_new_product",
        lambda item, product_id: captured.update(item=item, product_id=product_id) or {
            "synced": True,
            "catalog_row": {"product_id": "12345", "product_name": "عطر", "price": 99.0},
        },
    )
    result = ExportService(poster=lambda *a, **k: _Resp()).post_to_make(
        "http://hook", [{"أسم المنتج": "عطر", "سعر المنتج": 99}], envelope="data",
    )
    assert captured["product_id"] == 12345
    assert result["catalog_synced"] is True
    assert result["catalog_row"]["product_id"] == "12345"


def test_bulk_new_product_needs_per_item_confirmation(monkeypatch) -> None:
    class _Resp:
        status_code = 200
        text = '{"ok": true, "product_id": 12345}'

    monkeypatch.setattr(
        "services.export_service.sync_confirmed_new_product",
        lambda *_args: pytest.fail("bulk confirmation must not sync a single catalog row"),
    )
    result = ExportService(poster=lambda *a, **k: _Resp()).post_to_make(
        "http://hook", [{"أسم المنتج": "عطر 1"}, {"أسم المنتج": "عطر 2"}], envelope="data",
    )
    assert result["state"] == "accepted" and result["confirmed"] is False
    assert result["catalog_synced"] is False


def test_to_csv_returns_text() -> None:
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    csv = ExportService.to_csv(df)
    assert "a,b" in csv and "x" in csv
