"""tests/test_hidden_pages.py — حارس قائمة إخفاء الشريط الجانبي.

يثبّت أن ``HIDDEN_PAGES`` مفاتيح فعلية في ``PAGES`` (يمنع خطأً إملائياً صامتاً
يُبطل الإخفاء)، وأنها ثلاث بالضبط ولا تشمل الإعدادات ولا سلة المحذوفات
(المنفذان الوحيدان لضبط المفاتيح/الويبهوك واسترجاع المخفي).
"""
import app


def test_hidden_pages_are_real_page_keys():
    """كل عنصر إخفاء مفتاح موجود فعلاً في PAGES (وإلا الإخفاء بلا مفعول)."""
    missing = set(app.HIDDEN_PAGES) - set(app.PAGES)
    assert not missing, missing


def test_hidden_pages_exact_three():
    """ثلاث صفحات مخفية بالضبط."""
    assert len(app.HIDDEN_PAGES) == 3


def test_hidden_pages_content():
    """المخفي: أتمتة Make + الأتمتة الذكية + مصنع المنتجات."""
    assert app.HIDDEN_PAGES == frozenset(
        {"⚡ أتمتة Make", "🔄 الأتمتة الذكية", "✨ مصنع المنتجات"})


def test_settings_and_trash_not_hidden():
    """الإعدادات وسلة المحذوفات يبقيان ظاهرين (منفذا الضبط/الاسترجاع)."""
    assert "⚙️ الإعدادات" not in app.HIDDEN_PAGES
    assert "🗑️ سلة المحذوفات" not in app.HIDDEN_PAGES


def test_visible_labels_exclude_hidden():
    """القائمة المرئية = PAGES ناقص المخفي، وبلا أي تقاطع مع المخفي."""
    visible = [k for k in app.PAGES if k not in app.HIDDEN_PAGES]
    assert len(visible) == len(app.PAGES) - 3
    assert not (set(visible) & app.HIDDEN_PAGES)
