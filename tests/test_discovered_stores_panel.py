# -*- coding: utf-8 -*-
"""لوحة المتاجر المكتشفة: تصفية + وسم المُضاف + صمود الإضافة الجماعية.

البند 1 في ترتيب ``docs/خطة-العرض-الشاملة.md``. ثلاث فجوات كانت في لوحة واحدة:
حلقة عرض بلا حدّ، ولا عتبة (متجر بمنتجَين = متجر بـ5,000)، ونقرة لكل متجر.
المنطق خالص هنا عمداً — الرسم رفيع لا يحتاج اختباراً، والتصفية تحتاجه.
"""
import pytest

from ui.components.pagination import paginate
from ui.pages.scraper import (
    _DISC_PER_PAGE,
    _scrape_after_add,
    _try_add_discovered,
    added_message,
    filter_discovered,
)


def _store(sid: int, count: int, name: str = "", rc: int = 0) -> dict:
    return {"store_id": sid, "store_name": name, "product_count": count,
            "rating_avg": 4.5, "rating_count": rc}


FOUND = [_store(1, 5000, "عطور الفخامة"), _store(2, 300, "بيت العود"),
         _store(3, 2, "متجر صغير"), _store(4, 950, "الفخامة للعطور")]


# ── العتبة الدنيا ─────────────────────────────────────────────────────
def test_threshold_drops_tiny_stores():
    """الفجوة 1-ب: متجر بمنتجَين كان يظهر بنفس بروز متجر بـ5,000."""
    ids = [d["store_id"] for d in filter_discovered(FOUND, [], min_products=250)]
    assert ids == [1, 2, 4]


def test_threshold_zero_keeps_everything():
    assert len(filter_discovered(FOUND, [], min_products=0)) == 4


def test_threshold_is_inclusive_at_the_boundary():
    """حدّ 300 يُبقي متجر الـ300 — الحدّ «أدنى» لا «أكبر من»."""
    assert 2 in [d["store_id"] for d in filter_discovered(FOUND, [], min_products=300)]


# ── البحث ────────────────────────────────────────────────────────────
def test_search_by_name_matches_substring():
    ids = [d["store_id"] for d in filter_discovered(FOUND, [], query="الفخامة")]
    assert sorted(ids) == [1, 4]          # الاسمان يحتويانها بترتيب مختلف


def test_search_by_store_id():
    """أسماء متاجر «محلي» متشابهة، والمعرّف هو ما يميّزها فعلاً."""
    assert [d["store_id"] for d in filter_discovered(FOUND, [], query="3")] == [3]


def test_search_and_threshold_compose():
    rows = filter_discovered(FOUND, [], min_products=1000, query="الفخامة")
    assert [d["store_id"] for d in rows] == [1]   # الـ950 يسقط بالعتبة


def test_blank_query_is_not_a_filter():
    assert len(filter_discovered(FOUND, [], query="   ")) == 4


# ── وسم المُضاف ───────────────────────────────────────────────────────
def test_added_flag_marks_stores_already_in_the_list():
    """الفجوة 1-د: بعد الإضافة لم يكن شكل الصفّ يتغيّر."""
    rows = filter_discovered(FOUND, known_ids=[2, 4])
    assert {d["store_id"]: d["added"] for d in rows} == {
        1: False, 2: True, 3: False, 4: True}


def test_zero_and_none_known_ids_are_ignored():
    """متنافس بلا معرّف محلي (0/None) لا يجوز أن يوسم متجراً غير مضاف."""
    rows = filter_discovered(FOUND, known_ids=[0, None, 0])
    assert not any(d["added"] for d in rows)


def test_added_stores_are_not_hidden():
    """الوسم لا الحذف: المُضاف يبقى مرئياً كي لا يُعاد اكتشافه ويُضاف مرتين."""
    assert len(filter_discovered(FOUND, known_ids=[1, 2, 3, 4])) == 4


# ── سلامة المدخلات ────────────────────────────────────────────────────
def test_empty_and_missing_fields_are_safe():
    assert filter_discovered([], []) == []
    assert filter_discovered([{"store_id": 9}], [], min_products=1) == []   # count مفقود = 0


def test_source_rows_are_not_mutated():
    """الفلتر يُرجع نسخاً: تلويث صفوف الكاش يُفسد استدعاءً لاحقاً في نفس الرسم."""
    filter_discovered(FOUND, known_ids=[1])
    assert all("added" not in d for d in FOUND)


# ── الترقيم ──────────────────────────────────────────────────────────
def test_pagination_bounds_the_render_loop():
    """الفجوة 1-أ: الحلقة كانت ترسم كل متجر مكتشف بلا حدّ."""
    many = [_store(i, 100 + i) for i in range(25)]
    rows = filter_discovered(many, [])
    view = paginate(rows, 1, _DISC_PER_PAGE)
    assert len(view.items) == _DISC_PER_PAGE and view.total == 25
    assert len(paginate(rows, 3, _DISC_PER_PAGE).items) == 5    # الصفحة الأخيرة


# ── صمود الإضافة الجماعية ────────────────────────────────────────────
class _OkScraper:
    def add_competitor(self, name, url):
        return type("C", (), {"name": name, "mahally_store_id": 7})()


class _FailScraper:
    def add_competitor(self, name, url):
        raise ValueError("المتجر موجود مسبقاً")


def test_add_returns_error_instead_of_raising():
    """الأهمّ في الجماعي: فشل متجر واحد لا يُسقط بقيّة المحدَّدين."""
    comp, err = _try_add_discovered(_FailScraper(), _store(3, 2))
    assert comp is None and "موجود مسبقاً" in err


def test_add_success_returns_the_competitor():
    comp, err = _try_add_discovered(_OkScraper(), _store(5, 10, "متجر س"))
    assert err is None and comp.name == "متجر س"


def test_add_falls_back_to_a_name_when_the_store_has_none():
    """اسم فارغ لا يجوز أن يُنشئ منافساً بلا اسم (add_competitor يرفضه)."""
    comp, _err = _try_add_discovered(_OkScraper(), _store(8, 10, ""))
    assert comp.name == "متجر 8"


def test_bulk_partial_failure_counts_both_sides():
    """محاكاة الحلقة الجماعية: نجاح وفشل معاً بلا انقطاع."""
    class _Mixed:
        def add_competitor(self, name, url):
            if "2" in url:
                raise ValueError("موجود")
            return type("C", (), {"name": name, "mahally_store_id": 1})()

    done, failed = 0, []
    for d in (_store(1, 10), _store(2, 10), _store(3, 10)):
        _c, err = _try_add_discovered(_Mixed(), d)
        failed.append(err) if err else None
        done += 0 if err else 1
    assert done == 2 and len(failed) == 1


@pytest.mark.parametrize("bad", [None, "", 0])
def test_min_products_accepts_falsy_as_no_threshold(bad):
    assert len(filter_discovered(FOUND, [], min_products=bad)) == 4


# ── الكشط الفوري بعد الإضافة (الفجوة 1-هـ، أُكملت 2026-07-25) ───────────────
def test_message_without_scrape_points_to_the_nightly_round():
    """السلوك الافتراضي: أُضيف ولم يُكشط — الرسالة يجب أن تقول ذلك صراحةً."""
    msg = added_message("عطور الفخامة", 1, None)
    assert "٣:٣٠" in msg and "لم تُكشط" in msg


def test_message_with_scrape_reports_the_real_count():
    msg = added_message("بيت العود", 2, 1234)
    assert "1,234" in msg and "كُشط" in msg


def test_zero_saved_is_not_reported_as_success():
    """حارس ضدّ «النجاح الكاذب»: كشطٌ حفظ صفر منتج ليس نجاحاً يُعلَن."""
    msg = added_message("متجر فارغ", 9, 0)
    assert "لم يُحفظ أي منتج" in msg
    assert "منتجاً محفوظاً" not in msg


def test_scrape_failure_does_not_undo_the_add():
    """فشل الكشط يُعاد كتحذير — المتجر بقي مُضافاً وستلتقطه الجولة الليلية."""
    class _ScrapeFails:
        def scrape_and_save(self, comp):
            raise RuntimeError("انقطاع شبكة")

    saved, err = _scrape_after_add(_ScrapeFails(), object())
    assert saved is None and "انقطاع شبكة" in err


def test_scrape_after_add_returns_saved_count():
    class _ScrapeOk:
        def scrape_and_save(self, comp):
            return 77

    saved, err = _scrape_after_add(_ScrapeOk(), object())
    assert saved == 77 and err == ""
