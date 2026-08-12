"""tests/test_salla_api_catalog.py — سحب كتالوج المتجر من Salla (بلا شبكة).

يحقن دالة GET مزيّفة فيختبر التطبيع + ترقيم الصفحات + الأمان (بلا توكن/خطأ).
"""
import pytest

from utils import salla_api


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _two_page_get(pages):
    """يبني GET مزيّفاً يعيد صفحات حسب params['page']."""
    def _get(url, **kw):
        page = int(kw.get("params", {}).get("page", 1))
        return pages.get(page, _Resp({"data": []}))
    return _get


# ════════════════════════════════════════════════════════════════════
#  التطبيع
# ════════════════════════════════════════════════════════════════════
def test_norm_product_brand_dict() -> None:
    row = salla_api._norm_product(
        {"id": 5, "name": "ديور سوفاج 100مل", "sku": "D-1",
         "brand": {"id": 1, "name": "Dior"}, "barcode": "3348901250197"}
    )
    assert row["أسم المنتج"] == "ديور سوفاج 100مل"
    assert row["الماركة"] == "Dior"
    assert row["رمز المنتج sku"] == "D-1"
    assert row["الباركود"] == "3348901250197"
    assert row["id"] == 5


def test_norm_product_brand_string_and_gtin_fallback() -> None:
    row = salla_api._norm_product({"name": "x", "brand": "Lattafa", "gtin": "999999"})
    assert row["الماركة"] == "Lattafa"
    assert row["الباركود"] == "999999"


def test_norm_product_missing_name_is_none() -> None:
    assert salla_api._norm_product({"sku": "x"}) is None
    assert salla_api._norm_product("not a dict") is None


# ════════════════════════════════════════════════════════════════════
#  السحب + الترقيم
# ════════════════════════════════════════════════════════════════════
def test_fetch_no_token_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("SALLA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SALLA_API_TOKEN", raising=False)
    assert salla_api.fetch_store_products(_get=lambda *a, **k: _Resp({})) == []


def test_fetch_paginates_until_last_page(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    pages = {
        1: _Resp({"data": [{"name": "A"}, {"name": "B"}],
                  "pagination": {"currentPage": 1, "totalPages": 2}}),
        2: _Resp({"data": [{"name": "C"}],
                  "pagination": {"currentPage": 2, "totalPages": 2}}),
    }
    rows = salla_api.fetch_store_products(_get=_two_page_get(pages))
    assert [r["أسم المنتج"] for r in rows] == ["A", "B", "C"]


def test_fetch_stops_on_empty_data(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    pages = {1: _Resp({"data": [{"name": "A"}],
                       "pagination": {"currentPage": 1, "totalPages": 9}}),
             2: _Resp({"data": []})}
    rows = salla_api.fetch_store_products(_get=_two_page_get(pages))
    assert len(rows) == 1


def test_fetch_non_200_breaks(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    rows = salla_api.fetch_store_products(_get=lambda *a, **k: _Resp({}, status=401))
    assert rows == []


def test_store_catalog_dataframe(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    pages = {1: _Resp({"data": [{"name": "ديور سوفاج 100مل", "brand": {"name": "Dior"}}],
                       "pagination": {"currentPage": 1, "totalPages": 1}})}
    df = salla_api.store_catalog_dataframe(_get=_two_page_get(pages))
    assert df is not None
    assert list(df["أسم المنتج"]) == ["ديور سوفاج 100مل"]
    assert "الماركة" in df.columns


def test_store_catalog_dataframe_empty_is_none(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    assert salla_api.store_catalog_dataframe(_get=lambda *a, **k: _Resp({"data": []})) is None


# ════════════════════════════════════════════════════════════════════
#  update_product — تعديل موجّه (PUT) لمنتج قائم
# ════════════════════════════════════════════════════════════════════
def _capturing_put(store):
    def _put(url, **kw):
        store["url"] = url
        store["json"] = kw.get("json")
        return _Resp({"data": {"id": 7, "updated": True}})
    return _put


def test_update_product_sends_only_given_fields(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    cap = {}
    out = salla_api.update_product(
        7, _put=_capturing_put(cap),
        brand_id=12, metadata_title="عنوان", metadata_description="وصف",
    )
    assert out == {"id": 7, "updated": True}
    assert cap["url"].endswith("/products/7")
    assert cap["json"] == {"brand_id": 12, "metadata_title": "عنوان", "metadata_description": "وصف"}


def test_update_product_drops_empty_fields(monkeypatch) -> None:
    """التعديل الموجّه يُسقط الفارغ كي لا يمحو بيانات قائمة."""
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    cap = {}
    salla_api.update_product(7, _put=_capturing_put(cap), brand_id=12, metadata_title="", category_id=None)
    assert cap["json"] == {"brand_id": 12}          # الفارغ مُسقَط


def test_update_product_all_empty_is_noop(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    called = {"n": 0}
    def _put(*a, **k):
        called["n"] += 1
        return _Resp({})
    assert salla_api.update_product(7, _put=_put, metadata_title="", brand_id=None) is None
    assert called["n"] == 0                          # لا نداء شبكة أصلاً


def test_update_product_no_token(monkeypatch) -> None:
    monkeypatch.delenv("SALLA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SALLA_API_TOKEN", raising=False)
    assert salla_api.update_product(7, brand_id=12, _put=lambda *a, **k: _Resp({})) is None


# ════════════════════════════════════════════════════════════════════
#  fetch_brands / fetch_categories / sync_store_maps — حلّ الـIDs
# ════════════════════════════════════════════════════════════════════
def _maps_get(brands, cats):
    """GET مزيّف يعيد الماركات أو التصنيفات حسب الـendpoint."""
    def _get(url, **kw):
        if url.endswith("/brands"):
            return _Resp({"data": brands, "pagination": {"currentPage": 1, "totalPages": 1}})
        if url.endswith("/categories"):
            return _Resp({"data": cats, "pagination": {"currentPage": 1, "totalPages": 1}})
        return _Resp({"data": []})
    return _get


def test_fetch_brands_returns_name_id(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    g = _maps_get([{"id": 12, "name": "Dior"}, {"id": 13, "name": "Chanel"},
                   {"name": "بلا معرّف"}], [])
    out = salla_api.fetch_brands(_get=g)
    assert out == [{"name": "Dior", "id": 12}, {"name": "Chanel", "id": 13}]  # بلا-معرّف مُسقَط


def test_fetch_categories_returns_name_id(monkeypatch) -> None:
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    g = _maps_get([], [{"id": 5, "name": "عطور رجالية"}])
    assert salla_api.fetch_categories(_get=g) == [{"name": "عطور رجالية", "id": 5}]


def _isolate_maps(monkeypatch, tmp_path):
    import utils.missing_queue_manager as mqm
    monkeypatch.setattr(mqm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(mqm, "BRAND_CATALOG_FILE", tmp_path / "brand_catalog.csv")
    monkeypatch.setattr(mqm, "CATEGORY_CATALOG_FILE", tmp_path / "category_catalog.csv")
    return mqm


def test_sync_store_maps_writes_ids(monkeypatch, tmp_path) -> None:
    mqm = _isolate_maps(monkeypatch, tmp_path)
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    g = _maps_get([{"id": 12, "name": "Dior"}], [{"id": 5, "name": "عطور رجالية"}])
    res = salla_api.sync_store_maps(_get=g)
    # CSV هو المسار الحارّ؛ supabase=0 لأن SUPABASE غير مُهيّأ في بيئة الاختبار
    assert res == {"configured": True, "brands": 1, "categories": 1, "supabase": 0}
    assert mqm.load_brand_id_map().get(mqm._brand_key("Dior")) == "12"
    assert mqm.load_category_catalog().get("عطور رجالية") == "5"


def test_sync_store_maps_no_token(monkeypatch) -> None:
    monkeypatch.delenv("SALLA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SALLA_API_TOKEN", raising=False)
    assert salla_api.sync_store_maps()["configured"] is False


def test_synced_brand_resolves_to_payload_id(monkeypatch, tmp_path) -> None:
    """الاختبار القاطع: ماركة/تصنيف موجودان ⇒ الحمولة تحمل brand_id/category_id رقميين غير صفريين."""
    import pandas as pd
    import utils.make_helper as mh
    from services.export_service import to_new_products_payload
    _isolate_maps(monkeypatch, tmp_path)
    monkeypatch.setenv("SALLA_ACCESS_TOKEN", "tkn")
    # لا نداء شبكة عند أي miss
    monkeypatch.setattr(mh, "_create_brand_via_salla", lambda *a, **k: None)
    monkeypatch.setattr(mh, "_create_category_via_salla", lambda *a, **k: None)
    # مزامنة الخرائط من «سلة»
    salla_api.sync_store_maps(
        _get=_maps_get([{"id": 12, "name": "Dior"}], [{"id": 5, "name": "عطور رجالية"}]),
    )
    df = pd.DataFrame([{"منتج_المنافس": "ديور سوفاج", "سعر_المنافس": 450,
                        "الماركة": "Dior", "تصنيف_المنتج": "عطور رجالية"}])
    it = to_new_products_payload(df)[0]
    assert it["brand_id"] == 12 and it["brand_id"] != 0
    assert it["category_id"] == 5


# ════════════════════════════════════════════════════════════════════
#  Supabase mirror — upsert_store_maps (on conflict salla_id)
# ════════════════════════════════════════════════════════════════════
class _FakeTable:
    def __init__(self, calls, name):
        self._calls, self._name, self._rows, self._oc = calls, name, None, None

    def upsert(self, rows, on_conflict=None):
        self._rows, self._oc = rows, on_conflict
        return self

    def execute(self):
        self._calls.append((self._name, self._rows, self._oc))
        return {"data": self._rows}


class _FakeSupabase:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return _FakeTable(self.calls, name)


def test_upsert_store_maps_to_supabase() -> None:
    """الماركات ⇒ name_ar (مخطّط Supabase)، التصنيفات ⇒ name، on conflict salla_id."""
    from utils.supabase_client import upsert_store_maps
    fake = _FakeSupabase()
    n = upsert_store_maps(
        [{"id": 12, "name": "Dior"}], [{"id": 5, "name": "عطور رجالية"}], _client=fake,
    )
    assert n == 2
    tables = {c[0]: c for c in fake.calls}
    assert tables["store_brands"][1] == [{"salla_id": 12, "name_ar": "Dior"}]   # name_ar لا name
    assert tables["store_brands"][2] == "salla_id"            # on conflict
    assert tables["store_categories"][1] == [{"salla_id": 5, "name": "عطور رجالية"}]
    assert tables["store_categories"][2] == "salla_id"


def test_upsert_brand_bilingual_name_splits_ar_en() -> None:
    """اسم «عربي | English» ⇒ name_ar + name_en."""
    from utils.supabase_client import upsert_store_maps
    fake = _FakeSupabase()
    upsert_store_maps([{"id": 9, "name": "ديور | Dior"}], [], _client=fake)
    row = fake.calls[0][1][0]
    assert row == {"salla_id": 9, "name_ar": "ديور", "name_en": "Dior"}


def test_upsert_store_maps_no_client_is_noop() -> None:
    from utils.supabase_client import upsert_store_maps
    # بلا عميل (Supabase غير مُهيّأ) ⇒ 0 بلا خطأ
    assert upsert_store_maps([{"id": 1, "name": "X"}], [], _client=None) == 0
