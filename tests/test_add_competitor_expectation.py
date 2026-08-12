# -*- coding: utf-8 -*-
"""إضافة متجر لا تكشطه — والواجهة يجب أن تقول ذلك.

`ScraperService.add_competitor` يكتب في ملف المتاجر ثم يعود؛ لا كشط ولا بيانات.
بلا تصريح في الواجهة يظنّ المالك أن المنتجات وصلت فينتظر ما لا يأتي حتى الجولة
الليلية (MahwousNightlyScrape 03:30). حارسان: أن الخدمة فعلاً لا تكشط، وأن كلا
مسارَي الإضافة في الصفحة يُظهران التنبيه.
"""
import inspect
from pathlib import Path

from services.scraper_service import ScraperService
from ui.pages.scraper import _ADDED_NOT_SCRAPED, _render_add, _render_discovered


def test_add_competitor_does_not_scrape():
    """حارس السلوك: لو صار add_competitor يكشط، فالتنبيه يصير كذباً ويجب حذفه."""
    src = inspect.getsource(ScraperService.add_competitor)
    for scraping in ("scrape", "fetch_products", "save_products"):
        assert scraping not in src, (
            f"add_competitor صار يستدعي «{scraping}» — راجع نصّ التنبيه في الصفحة"
        )


def test_notice_names_the_nightly_round_and_the_manual_way():
    """التنبيه يفيد فقط إن قال **متى** تأتي البيانات وكيف تُستعجَل."""
    assert "٣:٣٠" in _ADDED_NOT_SCRAPED           # موعد الجولة الليلية
    assert "اكشطه الآن" in _ADDED_NOT_SCRAPED     # المخرج اليدوي


def test_both_add_paths_show_the_notice():
    """مسارا الإضافة: النموذج اليدوي ولوحة المتاجر المكتشفة."""
    for fn in (_render_add, _render_discovered):
        src = inspect.getsource(fn)
        assert "_ADDED_NOT_SCRAPED" in src, (
            f"{fn.__name__} يُعلن نجاح الإضافة بلا تنبيه «لم يُكشط بعد»"
        )


def test_notice_follows_every_success_banner():
    """حارس ضدّ إضافة مسار ثالث ينسى التنبيه: كل «أُضيف» يتبعه التنبيه."""
    src = Path(inspect.getfile(_render_add)).read_text(encoding="utf-8")
    successes = [i for i, ln in enumerate(src.splitlines()) if "أُضيف «" in ln]
    assert successes, "لم أجد لافتة نجاح الإضافة — تغيّرت الصفحة، حدّث الاختبار"
    lines = src.splitlines()
    for i in successes:
        assert "_ADDED_NOT_SCRAPED" in lines[i + 1], (
            f"لافتة نجاح في السطر {i + 1} بلا تنبيه بعدها"
        )
