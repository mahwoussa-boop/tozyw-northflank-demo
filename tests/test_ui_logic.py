"""tests/test_ui_logic.py — اختبارات منطق الواجهة الخالص (P4) دون Streamlit."""
import pandas as pd

from core.enums import SectionType
from ui.components.action_bar import (
    _delivery_identifiers, chunk_payload, page_keys, send_in_batches,
)
from ui.components.card_actions import _append_confirmed_catalog_row
from ui.components.filter_bar import Filters, apply_filters, options_for
from ui.components.pagination import paginate
from ui.components.product_card import build_card, card_tool_links
from ui.components.salla_export import salla_export_summary
from ui.components.status_badge import confidence_badge, section_badge
from ui.reconcile import (
    apply_reconciliation,
    attach_to_sections,
    attachments_from_duplicates,
    clean_missing,
    duplicate_keys,
)
from ui.review_ai import (
    build_pair,
    classify_rows,
    confirmed_duplicates_df,
    finalize_verdict,
    normalize_verdict,
    parse_batch_response,
    redistribute_reviews,
)
from ui.state_manager import AppState, DictStore, stable_key


# ── state_manager ──
def test_stable_key_and_hide_unhide() -> None:
    assert stable_key(" عطر ") == "softdel_عطر"
    state = AppState()
    state.hide("عطر شانيل")
    assert state.is_hidden("عطر شانيل")
    state.unhide("عطر شانيل")
    assert not state.is_hidden("عطر شانيل")


def test_state_save_load_roundtrip() -> None:
    store = DictStore()
    state = AppState.load(store)
    state.hide("منتج")
    state.mark_price_processed("SKU1")
    state.save(store)
    reloaded = AppState.load(store)
    assert reloaded.is_hidden("منتج") and reloaded.is_price_processed("SKU1")


def test_state_normalize_corrupt_sets() -> None:
    store = DictStore({"_app_state_v2": AppState(hidden_products=None)})  # type: ignore
    state = AppState.load(store)
    assert isinstance(state.hidden_products, set)


# ── pagination ──
def test_paginate_list_and_dataframe() -> None:
    view = paginate(list(range(30)), page=2, per_page=12)
    assert view.items == list(range(12, 24))
    assert view.total_pages == 3 and view.start == 12 and view.end == 24
    df = pd.DataFrame({"x": range(30)})
    dview = paginate(df, page=3, per_page=12)
    assert len(dview.items) == 6 and dview.page == 3


def test_paginate_clamps_overflow_and_empty() -> None:
    assert paginate(list(range(5)), page=99, per_page=10).page == 1
    empty = paginate([], page=1, per_page=10)
    assert empty.total == 0 and empty.total_pages == 1 and empty.caption == "لا عناصر"


# ── filter_bar ──
def test_apply_filters_search_brand_price() -> None:
    df = pd.DataFrame({
        "المنتج": ["عطر شانيل", "عطر ديور", "ماء"],
        "الماركة": ["Chanel", "Dior", "Chanel"],
        "السعر": [100, 200, 50],
    })
    assert len(apply_filters(df, Filters(search="ديور"))) == 1
    assert len(apply_filters(df, Filters(brand="Chanel"))) == 2
    assert len(apply_filters(df, Filters(min_price=80, max_price=150))) == 1
    assert options_for(df, "الماركة") == ["الكل", "Chanel", "Dior"]


def test_filters_active_chips() -> None:
    chips = Filters(search="x", brand="Chanel").active_chips
    assert any("Chanel" in c for c in chips) and any("x" in c for c in chips)


def test_apply_filters_competitors_and_risk() -> None:
    df = pd.DataFrame({
        "المنتج": ["a", "b", "c"],
        "عدد_المنافسين": [0, 3, 10],
        "الخطورة": ["", "🟡 متوسط", "🔴 حرج"],
    })
    assert len(apply_filters(df, Filters(min_competitors=3))) == 2
    assert len(apply_filters(df, Filters(risk="🔴 حرج"))) == 1
    # غياب العمود = لا فلترة (لا انهيار)
    bare = pd.DataFrame({"المنتج": ["a"]})
    assert len(apply_filters(bare, Filters(min_competitors=5, risk="🔴 حرج"))) == 1
    chips = Filters(min_competitors=3, risk="🔴 حرج").active_chips
    assert any("≥3" in c for c in chips) and any("حرج" in c for c in chips)


# ── status_badge ──
def test_badges() -> None:
    assert confidence_badge(90)[1] == "green"
    assert confidence_badge(70)[1] == "orange"
    assert confidence_badge(40)[1] == "gray"
    label, color, icon = section_badge(SectionType.PRICE_RAISE)
    assert color == "red" and icon == "🔴"


# ── product_card ──
def test_build_card_extracts_fields() -> None:
    row = {"المنتج": "عطر", "السعر": "100", "معرف_المنتج": "S1",
           "منتج_المنافس": "عطر منافس", "سعر_المنافس": 120, "المنافس": "متجر",
           "نسبة_التطابق": 95, "الفرق": -20}
    card = build_card(row)
    assert card.our_name == "عطر" and card.our_price == 100.0
    assert card.comp_store == "متجر" and card.match_pct == 95.0


def test_card_tool_links_and_page_urls() -> None:
    from ui.components.product_card import our_page_url
    row = {"المنتج": "عطر ديور", "منتج_المنافس": "عطر ديور منافس",
           "رابط_منتجنا": "https://mahwous.com/p/dior",
           "رابط_المنافس": "https://comp.example/dior"}
    card = build_card(row)
    # الأدوات = بحث السوق فقط (التنقّل صار بالنقر على الاسم ⇒ لا تكرار)
    links = dict(card_tool_links(card))
    assert list(links.keys()) == ["🌐 بحث في السوق"]
    market = links["🌐 بحث في السوق"]
    assert market.startswith("https://www.google.com/search?q=") and "%D8" in market
    # اسم المنافس قابل للنقر ← رابط صفحة المنافس (build_card يقرأ رابط_المنافس)
    assert card.comp_link == "https://comp.example/dior"
    # اسم منتجنا قابل للنقر ← الرابط المباشر إن توفّر
    assert our_page_url(card) == "https://mahwous.com/p/dior"

    # رابط منتجنا غائب ⇒ بحث مُوجَّه site:mahwous.com بدل رابط مكسور
    card2 = build_card({"المنتج": "عطر شانيل", "رابط_منتجنا": "", "رابط_المنافس": ""})
    u = our_page_url(card2)
    assert "site" in u and "mahwous.com" in u
    assert card2.comp_link == ""  # رابط المنافس غائب (صفوف المستبعدين) ⇒ اسم غير قابل للنقر

    # بلا أي اسم ⇒ لا أدوات ولا رابط منتج
    assert card_tool_links(build_card({})) == []
    assert our_page_url(build_card({})) == ""


def test_transparency_counts() -> None:
    from ui.pages._section_page import transparency_counts
    df = pd.DataFrame({"المنتج": ["A", "B", "C", "D"],
                       "معرف_المنتج": ["s1", "s2", "s3", "s4"]})
    state = AppState()
    state.hide("A")            # مخفي واحد
    state.mark_price_processed("s2")  # معالَج واحد
    # «معروض» يستبعد المخفيّ والمعالَج معاً ⇒ 4 − 2 = 2
    c = transparency_counts(df, state)
    assert c == {"exists": 4, "shown": 2, "hidden": 1, "processed": 1}

    # فارغ ⇒ أصفار، وغياب الأعمدة آمن
    assert transparency_counts(pd.DataFrame(), AppState()) == {
        "exists": 0, "shown": 0, "hidden": 0, "processed": 0}
    no_cols = pd.DataFrame({"x": [1, 2]})
    assert transparency_counts(no_cols, AppState()) == {
        "exists": 2, "shown": 2, "hidden": 0, "processed": 0}


def test_visible_dataframe_drops_hidden_and_processed() -> None:
    from ui.pages._section_page import visible_dataframe
    df = pd.DataFrame({"المنتج": ["A", "B", "C"], "معرف_المنتج": ["s1", "s2", "s3"]})
    state = AppState()
    state.hide("A")
    state.mark_price_processed("s3")
    visible, removed = visible_dataframe(df, state)
    assert removed == 2 and visible["المنتج"].tolist() == ["B"]


def test_state_log_action_and_undo() -> None:
    state = AppState()
    state.log_action(key="s1", name="عطر", action="تحديث السعر",
                     detail="120 ر.س", kind="price")
    state.log_action(key="s1", name="عطر", action="تحديث السعر",
                     detail="130 ر.س", kind="price")  # نفس (مفتاح، إجراء) ⇒ يستبدل
    assert len(state.processed_log) == 1
    assert state.processed_log[0]["detail"] == "130 ر.س"  # الأحدث
    state.log_action(key="u1", name="منافس", action="تجاهل/إخفاء", kind="hidden")
    assert len(state.processed_log) == 2 and state.processed_log[0]["key"] == "u1"
    state.remove_log("s1")
    assert [e["key"] for e in state.processed_log] == ["u1"]


def test_processed_entries_merges_legacy_sets() -> None:
    from ui.pages.processed import processed_entries
    state = AppState()
    state.log_action(key="s1", name="عطر", action="تحديث السعر", kind="price")
    state.processed_price_skus.update({"s1", "s2"})   # s1 مُسجَّل، s2 قديم بلا سجلّ
    state.processed_missing_urls.add("http://c/x")
    entries = processed_entries(state)
    keys = {e["key"] for e in entries}
    assert keys == {"s1", "s2", "http://c/x"}
    # s1 يحتفظ بسجلّه الغنيّ (لا يُكرَّر)
    assert sum(1 for e in entries if e["key"] == "s1") == 1


def test_active_missing_drops_sent_and_hidden() -> None:
    from ui.pages.missing import active_missing
    df = pd.DataFrame({
        "منتج_المنافس": ["P1", "P2", "P3"],
        "رابط_المنافس": ["http://c/1", "http://c/2", "http://c/3"],
    })
    state = AppState()
    state.mark_missing_processed("http://c/1")  # أُرسل
    state.hide("P3")                            # أُخفي
    out = active_missing(df, state)
    assert out["منتج_المنافس"].tolist() == ["P2"]


def test_missing_row_key_falls_back_to_name() -> None:
    from ui.pages.missing import missing_row_key
    # رابط صالح ⇒ يُستخدم كما هو
    assert missing_row_key({"رابط_المنافس": "http://c/1"}) == "http://c/1"
    # رابط مفقود/«nan» ⇒ مفتاح اسم مستقر «name::الاسم»
    assert missing_row_key({"رابط_المنافس": "nan", "منتج_المنافس": "P9"}) == "name::P9"
    assert missing_row_key({"منتج_المنافس": "P9"}) == "name::P9"
    # لا رابط ولا اسم ⇒ لا مفتاح مستقر
    assert missing_row_key({}) == ""
    assert missing_row_key({"رابط_المنافس": "", "منتج_المنافس": ""}) == ""


def test_active_missing_drops_no_link_marked_by_name() -> None:
    """منتج مفقود بلا رابط منافس يُعلَّم بمفتاح اسم ⇒ يختفي من المفقودات (إصلاح #2)."""
    from ui.pages.missing import active_missing, missing_row_key
    df = pd.DataFrame({
        "منتج_المنافس": ["P1", "P2"],
        "رابط_المنافس": ["", ""],          # كلاهما بلا رابط منافس
    })
    state = AppState()
    state.mark_missing_processed(missing_row_key(df.iloc[0]))  # علّم P1 بالاسم
    out = active_missing(df, state)
    assert out["منتج_المنافس"].tolist() == ["P2"]


# ── action_bar ──
def test_page_keys() -> None:
    df = pd.DataFrame({"معرف_المنتج": ["a", " b ", ""]})
    assert page_keys(df, "معرف_المنتج") == ["a", "b"]
    assert page_keys(pd.DataFrame(), "x") == []


def test_chunk_payload() -> None:
    assert chunk_payload([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunk_payload([], 2) == []
    assert chunk_payload([1, 2, 3], 0) == [[1, 2, 3]]


class _StubService:
    """خدمة Make وهمية (بلا شبكة): تنجح/تفشل حسب جدول النتائج لكل نداء."""

    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.calls: list[list] = []
        self.envelopes: list[str] = []

    def post_to_make(self, url: str, products: list, *, envelope: str = "products") -> dict:
        self.calls.append(products)
        self.envelopes.append(envelope)
        ok = self._results.pop(0) if self._results else False
        return {"success": ok, "status_code": 200 if ok else 500}


def test_send_in_batches_aggregates() -> None:
    # 5 منتجات، دفعة 2 ⇒ 3 دفعات كلها تنجح من أول محاولة.
    svc = _StubService([True, True, True])
    out = send_in_batches(
        svc, "http://x", [1, 2, 3, 4, 5], batch_size=2, sleep=lambda _s: None,
    )
    assert out == {
        "success": True, "sent": 5, "accepted": 0, "failed": 0, "total": 5, "errors": [],
        "blocked_low_price": [], "held_outside_band": [], "held_low_quality": [],
    }
    assert [len(c) for c in svc.calls] == [2, 2, 1]
    assert svc.envelopes == ["products", "products", "products"]  # المظروف الافتراضي


def test_send_in_batches_retries_then_fails() -> None:
    # دفعة واحدة تفشل 3 محاولات ⇒ تُحتسب فاشلة (3 نداءات) + سبب الرفض الحقيقي.
    svc = _StubService([False, False, False])
    out = send_in_batches(
        svc, "http://x", [1, 2], batch_size=5, max_retries=3, sleep=lambda _s: None,
    )
    assert out == {
        "success": False, "sent": 0, "accepted": 0, "failed": 2, "total": 2, "errors": ["500"],
        "blocked_low_price": [], "held_outside_band": [], "held_low_quality": [],
    }
    assert len(svc.calls) == 3


def test_send_in_batches_threads_envelope() -> None:
    # المنتجات الجديدة تُرسَل بمظروف «data» (مخطط CreateProduct) لا «products».
    svc = _StubService([True])
    send_in_batches(
        svc, "http://x", [1], batch_size=5, sleep=lambda _s: None, envelope="data",
    )
    assert svc.envelopes == ["data"]


def test_send_in_batches_does_not_retry_accepted_delivery() -> None:
    class _AcceptedService:
        calls = 0

        def post_to_make(self, _url, _chunk, **_kwargs):
            self.calls += 1
            return {"success": True, "confirmed": False, "state": "accepted"}

    svc = _AcceptedService()
    out = send_in_batches(
        svc, "http://x", [1, 2], batch_size=2, max_retries=3, sleep=lambda _s: None,
    )
    assert svc.calls == 1
    assert out["sent"] == 0 and out["accepted"] == 2 and out["failed"] == 0


def test_send_in_batches_exposes_only_confirmed_payload_to_callback() -> None:
    class _ConfirmedService:
        def post_to_make(self, _url, chunk, **_kwargs):
            return {
                "success": True, "confirmed": True,
                "delivered_items": [chunk[0]],
            }

    confirmed: list[int] = []
    out = send_in_batches(
        _ConfirmedService(), "http://x", [1, 2], batch_size=2,
        sleep=lambda _s: None, confirmed_items_cb=confirmed.extend,
    )
    assert out["sent"] == 2
    assert confirmed == [1]


def test_delivery_identifiers_use_payload_identity() -> None:
    assert _delivery_identifiers([
        {"NO": "42", "name": "عطر A"},
        {"product_id": 7, "اسم المنتج": "عطر B"},
    ]) == ({"42", "7"}, {"عطر A", "عطر B"})


def test_confirmed_catalog_row_updates_session_catalog_without_duplicate() -> None:
    state = AppState()
    state.our_catalog = pd.DataFrame({
        "معرف_المنتج": ["1"], "المنتج": ["قديم"], "السعر": [10.0],
    })
    assert _append_confirmed_catalog_row(state, {
        "product_id": "2", "product_name": "عطر جديد", "price": 99,
    })
    assert state.our_catalog["معرف_المنتج"].tolist() == ["1", "2"]
    assert _append_confirmed_catalog_row(state, {
        "product_id": "2", "product_name": "عطر جديد", "price": 101,
    })
    assert len(state.our_catalog) == 2
    assert state.our_catalog.loc[1, "السعر"] == 101.0


class _FloorGuardStubService:
    """خدمة وهمية تحاكي send_floor_guard: تحجز عنصراً واحداً من كل دفعة ناجحة."""

    def post_to_make(self, url: str, products: list, *, envelope: str = "products") -> dict:
        blocked = [products[0]] if products else []
        return {"success": True, "status_code": 200, "blocked_low_price": blocked}


def test_send_in_batches_surfaces_blocked_low_price() -> None:
    # أ2: العناصر المحجوزة (send_floor_guard) تُجمَع في الناتج ولا تُحتسَب «مُرسَلة».
    out = send_in_batches(
        _FloorGuardStubService(), "http://x", [1, 2, 3, 4],
        batch_size=2, sleep=lambda _s: None,
    )
    assert out["blocked_low_price"] == [1, 3]
    assert out["sent"] == 2  # 4 إجمالي − 2 محجوزَين
    assert out["success"] is True


class _BandGuardStubService:
    """خدمة وهمية تحاكي send_band_guard: تحجز عنصراً واحداً من كل دفعة ناجحة."""

    def post_to_make(self, url: str, products: list, *, envelope: str = "products") -> dict:
        held = [products[0]] if products else []
        return {"success": True, "status_code": 200, "held_outside_band": held}


def test_send_in_batches_surfaces_held_outside_band() -> None:
    # أ4 (مرحلة 1): العناصر المحجوزة (send_band_guard) تُجمَع ولا تُحتسَب «مُرسَلة».
    out = send_in_batches(
        _BandGuardStubService(), "http://x", [1, 2, 3, 4],
        batch_size=2, sleep=lambda _s: None,
    )
    assert out["held_outside_band"] == [1, 3]
    assert out["sent"] == 2  # 4 إجمالي − 2 محجوزَين
    assert out["success"] is True


# ── criticality (الأهمية القصوى — نقي، بلا Streamlit) ──
def test_criticality_from_engine_risk() -> None:
    from ui.components.criticality import (
        CRITICAL, MEDIUM, NORMAL, criticality_of_row,
    )
    assert criticality_of_row({"الخطورة": "🔴 حرج"}) == CRITICAL
    assert criticality_of_row({"الخطورة": "🟡 متوسط"}) == MEDIUM
    assert criticality_of_row({"الخطورة": "🟢 منخفض"}) == NORMAL
    assert criticality_of_row({}) == NORMAL


def test_criticality_huge_gap_escalates() -> None:
    from ui.components.criticality import CRITICAL, MEDIUM, criticality_of_row
    # فجوة 100% ⇒ حرج حتى مع خلو عمود الخطورة (مسار المفقودات).
    assert criticality_of_row({"السعر": 200, "سعر_المنافس": 100, "الفرق": 100}) == CRITICAL
    # فجوة ~15% ⇒ متوسط.
    assert criticality_of_row({"السعر": 115, "سعر_المنافس": 100, "الفرق": 15}) == MEDIUM


def test_criticality_only_escalates_never_downgrades() -> None:
    from ui.components.criticality import CRITICAL, criticality_of_row
    # المحرك «حرج» لكن الفجوة صغيرة ⇒ يبقى حرج (لا نُخفّض درجة المحرك).
    assert criticality_of_row(
        {"الخطورة": "🔴 حرج", "السعر": 101, "سعر_المنافس": 100, "الفرق": 1}
    ) == CRITICAL


def test_competitor_out_of_stock_hook() -> None:
    from ui.components.criticality import (
        CRITICAL, competitor_out_of_stock, criticality_of_row,
    )
    assert competitor_out_of_stock({"availability": "out of stock"}) is True
    assert competitor_out_of_stock({"المخزون": "نفد"}) is True
    assert competitor_out_of_stock({"in_stock": "false"}) is True
    assert competitor_out_of_stock({"availability": "in stock"}) is False
    assert competitor_out_of_stock({}) is False
    # الخطّاف يُصعّد للحرج حتى مع تساوي الأسعار.
    assert criticality_of_row(
        {"availability": "out_of_stock", "السعر": 100, "سعر_المنافس": 100}
    ) == CRITICAL


def test_criticality_filter_and_counts() -> None:
    from ui.components.criticality import (
        CRITICAL, MEDIUM, NORMAL, criticality_counts, filter_by_criticality,
    )
    df = pd.DataFrame([
        {"الخطورة": "🔴 حرج"}, {"الخطورة": "🟡 متوسط"}, {"الخطورة": "🟢 منخفض"},
    ])
    counts = criticality_counts(df)
    assert counts[CRITICAL] == 1 and counts[MEDIUM] == 1 and counts[NORMAL] == 1
    assert len(filter_by_criticality(df, CRITICAL)) == 1
    assert len(filter_by_criticality(df, "الكل")) == 3


def test_criticality_badge_shape() -> None:
    from ui.components.criticality import CRITICAL, row_criticality_badge
    label, color, tooltip = row_criticality_badge({"الخطورة": "🔴 حرج"})
    assert label == CRITICAL and color == "#EF4444" and "حرج" in tooltip


# ── salla_export (باني محقون ⇒ لا utils/Streamlit/شبكة) ──
def test_salla_export_summary_empty() -> None:
    assert salla_export_summary(pd.DataFrame()) == (b"", 0, 0)
    assert salla_export_summary(None) == (b"", 0, 0)


def test_salla_export_summary_counts_and_excluded() -> None:
    src = pd.DataFrame({"المنتج": ["a", "b", "c", "d", "e"]})
    calls: list = []

    def _fake_builder(missing_df, our_catalog_df, verify_missing):
        calls.append((len(missing_df), our_catalog_df, verify_missing))
        return b"csv-bytes", 3, pd.DataFrame()  # بوّابة الجودة أبقت 3 من 5

    data, exported, excluded = salla_export_summary(src, builder=_fake_builder)
    assert (data, exported, excluded) == (b"csv-bytes", 3, 2)
    # verify_missing=False افتراضاً (المفقودات أصلاً منقّاة)
    assert calls == [(5, None, False)]


def test_salla_export_summary_excluded_never_negative() -> None:
    src = pd.DataFrame({"المنتج": ["a", "b"]})

    def _over_builder(missing_df, our_catalog_df, verify_missing):
        return bytearray(b"x"), 9, pd.DataFrame()  # مُصدَّر > المصدر (لا يحدث عملياً)

    data, exported, excluded = salla_export_summary(src, builder=_over_builder)
    assert exported == 9 and excluded == 0 and data == b"x"


# ── reconcile (مطابقة فحص المستخدم — معطيات تركيبية، بلا CSV/AI/شبكة) ──
def _missing_df() -> pd.DataFrame:
    return pd.DataFrame({
        "منتج_المنافس": ["A", "B", "C", "D"],
        "مستوى_الثقة": ["green", "green", "review", "green"],
        "سعر_المنافس": ["100", "200", "300", "400"],
    })


def _dup_df() -> pd.DataFrame:
    # B و D مكرران ⇒ يطابقان منتجَي متجرنا «منتج-١» و«منتج-٢»
    return pd.DataFrame({
        "منتج_المنافس": ["B", "D"],
        "سعر_المنافس": ["200", "400"],
        "المنافس": ["متجر-س", "متجر-ص"],
        "الماركة": ["شانيل", "ديور"],
        "صورة_المنافس": ["http://img/b.jpg", "http://img/d.jpg"],
        "منتج_مطابق_محتمل": ["منتج-١", "منتج-٢"],
        "حجم_المنافس_مل": ["100", "50"],
        "نوع_المنافس": ["retail", "retail"],
        "جنس_المنافس": ["female", "male"],
        "درجة_تشابه_محسوبة": ["95", "90"],
    })


def _sections() -> dict:
    return {
        "🔴 سعر أعلى": pd.DataFrame({
            "المنتج": ["منتج-١", "منتج-آخر"],
            "جميع_المنافسين": [[{"name": "موجود", "competitor": "x"}], []],
            "عدد_المنافسين": [1, 0],
        }),
        "⚪ مستبعد": pd.DataFrame({
            "المنتج": ["منتج-٢"],
            "جميع_المنافسين": [[]],
            "عدد_المنافسين": [0],
        }),
    }


def test_duplicate_keys_safe() -> None:
    assert duplicate_keys(_dup_df()) == {"B", "D"}
    assert duplicate_keys(None) == set()
    assert duplicate_keys(pd.DataFrame({"x": [1]})) == set()


def test_clean_missing_removes_only_duplicates() -> None:
    out = clean_missing(_missing_df(), {"B", "D"})
    assert list(out["منتج_المنافس"]) == ["A", "C"]  # review C يبقى — لا يُخفى
    # لا مفاتيح ⇒ بلا تغيير ؛ عمود مفقود ⇒ آمن
    assert len(clean_missing(_missing_df(), set())) == 4
    assert clean_missing(pd.DataFrame(), {"B"}).empty


def test_attachments_keyed_by_matched_store() -> None:
    amap = attachments_from_duplicates(_dup_df())
    assert set(amap) == {"منتج-١", "منتج-٢"}
    comp = amap["منتج-١"][0]
    assert comp["name"] == "B" and comp["competitor"] == "متجر-س"
    assert comp["image_url"] == "http://img/b.jpg" and comp["size"] == "100"
    assert comp["_تطابق_مؤكد"] is True
    assert attachments_from_duplicates(None) == {}


def test_attach_to_sections_appends_and_bumps_count() -> None:
    secs = attach_to_sections(_sections(), attachments_from_duplicates(_dup_df()))
    raise_df = secs["🔴 سعر أعلى"]
    row = raise_df[raise_df["المنتج"] == "منتج-١"].iloc[0]
    assert len(row["جميع_المنافسين"]) == 2 and row["عدد_المنافسين"] == 2  # 1 موجود + 1 ملحق
    # صفّ غير مطابق لم يُمسّ
    other = raise_df[raise_df["المنتج"] == "منتج-آخر"].iloc[0]
    assert other["عدد_المنافسين"] == 0 and other["جميع_المنافسين"] == []
    # المستبعد اكتسب منافسه الأول (لكن القرار لم يتغيّر)
    exc = secs["⚪ مستبعد"].iloc[0]
    assert len(exc["جميع_المنافسين"]) == 1 and exc["عدد_المنافسين"] == 1


def test_attach_is_idempotent() -> None:
    amap = attachments_from_duplicates(_dup_df())
    once = attach_to_sections(_sections(), amap)
    twice = attach_to_sections(once, amap)
    r1 = once["🔴 سعر أعلى"]; r2 = twice["🔴 سعر أعلى"]
    a = r1[r1["المنتج"] == "منتج-١"].iloc[0]
    b = r2[r2["المنتج"] == "منتج-١"].iloc[0]
    assert len(a["جميع_المنافسين"]) == len(b["جميع_المنافسين"]) == 2  # لا تكرار إلحاق
    assert b["عدد_المنافسين"] == 2


def test_apply_reconciliation_stats() -> None:
    nm, ns, stats = apply_reconciliation(
        _missing_df(), _sections(), dup_df=_dup_df(), uni_df=pd.DataFrame({"x": [1, 2, 3]}),
    )
    assert stats["removed_duplicates"] == 2
    assert stats["attached_competitors"] == 2 and stats["attached_products"] == 2
    assert stats["missing_before"] == 4 and stats["missing_after"] == 2
    assert stats["unique_listed"] == 3
    assert list(nm["منتج_المنافس"]) == ["A", "C"]


# ── review_ai (المرحلة B — خدمة AI وهمية محقونة، بلا شبكة/كلفة) ──
class _MockAI:
    """خدمة AI وهمية: تُعيد رداً ثابتاً وتعدّ النداءات (بلا شبكة)."""

    def __init__(self, response: str, *, success: bool = True) -> None:
        self._response = response
        self._success = success
        self.calls = 0

    def call(self, prompt: str, system: str = "", **_kw):
        self.calls += 1
        return type("R", (), {"success": self._success, "response": self._response})()


def _review_rows() -> list[dict]:
    return [
        {"منتج_المنافس": "عطر س", "منتج_مطابق_محتمل": "منتج-١",
         "الماركة": "شانيل", "سعر_المنافس": "100"},
        {"منتج_المنافس": "عطر ص", "منتج_مطابق_محتمل": "منتج-٢",
         "الماركة": "ديور", "سعر_المنافس": "200"},
    ]


def test_build_pair_safe() -> None:
    pair = build_pair({"منتج_المنافس": "x", "منتج_مطابق_محتمل": "y", "الماركة": "z"})
    assert pair == {"comp": "x", "cand": "y", "brand": "z", "price": ""}
    assert build_pair({})["comp"] == ""


def test_normalize_verdict() -> None:
    assert normalize_verdict("مكرر") == "duplicate"
    assert normalize_verdict("DUPLICATE") == "duplicate"
    assert normalize_verdict("فريد") == "unique"
    assert normalize_verdict("شيء غامض") == "unsure"
    assert normalize_verdict(None) == "unsure"


def test_parse_batch_response_structured() -> None:
    resp = ('نص [{"i":0,"verdict":"مكرر","brand_match":true,"size_match":true},'
            '{"i":1,"verdict":"فريد"}] نهاية')
    out = parse_batch_response(resp, 2)
    assert out[0]["verdict"] == "duplicate" and out[0]["brand_match"] and out[0]["size_match"]
    assert out[1]["verdict"] == "unique"
    # رد تالف ⇒ unsure للجميع
    assert [d["verdict"] for d in parse_batch_response("لا JSON", 3)] == ["unsure"] * 3
    # فهرس ناقص ⇒ يبقى unsure
    miss = parse_batch_response('[{"i":1,"verdict":"مكرر"}]', 2)
    assert [d["verdict"] for d in miss] == ["unsure", "duplicate"]


def test_finalize_verdict_safety_guard() -> None:
    dup_ok = {"verdict": "duplicate", "brand_match": True, "size_match": True}
    assert finalize_verdict(dup_ok) == "duplicate"
    # «مكرر» بلا توافق ماركة أو حجم ⇒ يُخفَّض لـunsure (حارس — لا حذف خاطئ)
    assert finalize_verdict({"verdict": "duplicate", "brand_match": False,
                             "size_match": True}) == "unsure"
    assert finalize_verdict({"verdict": "duplicate", "brand_match": True,
                             "size_match": False}) == "unsure"
    # تعطيل الحارس ⇒ يبقى مكرر
    assert finalize_verdict({"verdict": "duplicate", "brand_match": False,
                             "size_match": False}, guard=False) == "duplicate"
    # «فريد» لا يتأثر بالحارس
    assert finalize_verdict({"verdict": "unique", "brand_match": False,
                             "size_match": False}) == "unique"


def test_classify_rows_with_mock() -> None:
    ai = _MockAI('[{"i":0,"verdict":"مكرر"},{"i":1,"verdict":"فريد"}]')
    out = classify_rows(_review_rows(), ai)
    assert out == ["duplicate", "unique"] and ai.calls == 1  # دفعة واحدة (≤8)


def test_classify_rows_ai_failure_is_unsure() -> None:
    ai = _MockAI("", success=False)
    assert classify_rows(_review_rows(), ai) == ["unsure", "unsure"]


def test_classify_rows_chunks() -> None:
    ai = _MockAI('[{"i":0,"verdict":"مكرر"}]')
    rows = _review_rows() * 3  # 6 صفوف، chunk=2 ⇒ 3 نداءات
    out = classify_rows(rows, ai, chunk_size=2)
    assert len(out) == 6 and ai.calls == 3


def test_confirmed_duplicates_df() -> None:
    df = confirmed_duplicates_df(_review_rows(), ["duplicate", "unique"])
    assert list(df["منتج_المنافس"]) == ["عطر س"]
    assert confirmed_duplicates_df(_review_rows(), ["unique", "unique"]).empty


def test_redistribute_reviews_end_to_end() -> None:
    missing = pd.DataFrame({
        "منتج_المنافس": ["عطر س", "عطر ص", "عطر ع"],
        "مستوى_الثقة": ["review", "review", "review"],
    })
    sections = {
        "🔴": pd.DataFrame({
            "المنتج": ["منتج-١"],
            "جميع_المنافسين": [[]],
            "عدد_المنافسين": [0],
        }),
    }
    nm, ns, stats = redistribute_reviews(
        missing, sections, _review_rows(), ["duplicate", "unique"],
    )
    assert stats["verdict_duplicate"] == 1 and stats["verdict_unique"] == 1
    assert stats["removed_duplicates"] == 1  # «عطر س» أُزيل
    assert list(nm["منتج_المنافس"]) == ["عطر ص", "عطر ع"]
    attached = ns["🔴"].iloc[0]
    assert len(attached["جميع_المنافسين"]) == 1 and attached["عدد_المنافسين"] == 1


def test_decision_records_and_corrections_fewshot() -> None:
    from ui.review_ai import (build_decision_records, corrections_to_fewshot,
                              verdict_to_arabic)
    recs = build_decision_records(_review_rows(), ["duplicate", "unique"])
    assert recs[0] == {"comp": "عطر س", "cand": "منتج-١",
                       "verdict": "duplicate", "source": "ai"}
    assert verdict_to_arabic("duplicate") == "مكرر" and verdict_to_arabic("x") == "غير مؤكد"
    # only user-source corrections become few-shot
    pool = [{"comp": "a", "cand": "b", "verdict": "duplicate", "source": "ai"},
            {"comp": "c", "cand": "d", "verdict": "unique", "source": "user"}]
    fs = corrections_to_fewshot(pool)
    assert fs == ({"comp": "c", "cand": "d", "verdict": "فريد"},)


def test_jsonl_roundtrip(tmp_path) -> None:
    from ui.review_ai import append_jsonl, read_jsonl
    path = tmp_path / "sub" / "log.jsonl"
    append_jsonl(path, [{"comp": "x", "verdict": "duplicate"}])
    append_jsonl(path, [{"comp": "y", "verdict": "unique"}])
    out = read_jsonl(path)
    assert [r["comp"] for r in out] == ["x", "y"]
    assert read_jsonl(tmp_path / "missing.jsonl") == []  # ملف غائب آمن


# ── review_panel (لوحة AI داخل التطبيق — منطق خالص) ──
def test_review_panel_progress_and_corrections() -> None:
    from ui.components.review_panel import (correction_record, latest_verdicts,
                                            progress_summary, unclassified_rows)
    decisions = [
        {"comp": "A", "cand": "x", "verdict": "duplicate", "source": "ai"},
        {"comp": "B", "cand": "y", "verdict": "unique", "source": "ai"},
        {"comp": "A", "cand": "x", "verdict": "unique", "source": "user"},  # تصحيح يطغى
    ]
    assert latest_verdicts(decisions) == {"A": "unique", "B": "unique"}
    summ = progress_summary(decisions)
    assert summ["classified"] == 2 and summ["unique"] == 2 and summ["corrections"] == 1
    rev = pd.DataFrame({"منتج_المنافس": ["A", "B", "C"], "مستوى_الثقة": ["review"] * 3})
    left = unclassified_rows(rev, decisions)
    assert [r["منتج_المنافس"] for r in left] == ["C"]
    rec = correction_record("X", "cand-x", "مكرر")
    assert rec == {"comp": "X", "cand": "cand-x", "verdict": "duplicate", "source": "user"}


def test_avail_badge_states() -> None:
    """شارة التوفّر: نفذت/متوفر تظهر فقط حين يكون out_of_stock مضبوطاً."""
    from ui.components.comparison_card import _avail_badge
    assert "نفذت" in _avail_badge({"out_of_stock": True})
    assert "متوفر" in _avail_badge({"out_of_stock": False})
    assert _avail_badge({}) == ""               # مجهول ⇒ لا شارة (لا تخمين)
    assert _avail_badge({"store": "x"}) == ""    # لا مفتاح توفّر ⇒ لا شارة


def test_data_age_badge_shows_only_past_stale_threshold() -> None:
    """شارة قِدَم البيانات (أ3 خطوة 2): تظهر فقط عند تجاوز STALE_AFTER_DAYS — لا ضجيج على الطازج."""
    from services.opportunity_service import STALE_AFTER_DAYS
    from ui.components.comparison_card import _data_age_badge
    assert _data_age_badge({}) == ""                                    # قِدَم غير معروف ⇒ لا شارة
    assert _data_age_badge({"data_age_days": 1.0}) == ""                 # طازج ⇒ لا شارة
    assert _data_age_badge({"data_age_days": STALE_AFTER_DAYS}) == ""    # على الحدّ بالضبط ⇒ لا شارة
    stale = _data_age_badge({"data_age_days": STALE_AFTER_DAYS + 2})
    assert "🕒" in stale and "يوم" in stale                              # تجاوز الحدّ ⇒ شارة نصية


def test_comparison_card_shows_availability() -> None:
    """البطاقة تُظهر شارة التوفّر لكل منافس عند ضبط out_of_stock."""
    from ui.components.comparison_card import build_comparison_card_html
    our = {"name": "عطرنا", "price": 100}
    comps = [
        {"name": "منافس أرخص", "price": 80, "store": "س1", "out_of_stock": True},
        {"name": "منافس آخر", "price": 120, "store": "س2", "out_of_stock": False},
    ]
    html_out = build_comparison_card_html(our, comps)
    assert "نفذت" in html_out and "متوفر" in html_out


def test_filter_by_availability() -> None:
    """فلتر التوفّر: نفذت/متوفر عبر مجموعة الروابط النافذة."""
    import pandas as pd
    from ui.components.filter_bar import filter_by_availability
    from conf.constants import COL_COMP_LINK
    df = pd.DataFrame({
        "المنتج": ["a", "b", "c"],
        COL_COMP_LINK: ["u1", "u2", "u3"],
    })
    oos = {"u2"}
    assert list(filter_by_availability(df, "🔴 نفذت", oos)["المنتج"]) == ["b"]
    assert list(filter_by_availability(df, "🟢 متوفر", oos)["المنتج"]) == ["a", "c"]
    # الكل / مجموعة فارغة ⇒ بلا فلترة
    assert len(filter_by_availability(df, "الكل", oos)) == 3
    assert len(filter_by_availability(df, "🔴 نفذت", set())) == 3
