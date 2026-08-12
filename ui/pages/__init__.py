"""حزمة ui.pages — صفحات رفيعة تصل الخدمات بالمكوّنات (لا منطق عمل).

كل صفحة تُصدّر ``render(...)``. الأقسام السعرية/المراجعة/المستبعد أغلفة
رقيقة فوق ``_section_page.render_section_page``.
"""
from ui.pages import (
    approved,
    dashboard,
    excluded,
    make_automation,
    missing,
    new_at_competitors,
    price_lower,
    price_raise,
    processed,
    product_factory,
    redistribute,
    review,
    settings,
    smart_automation,
    trash_bin,
)

__all__ = [
    "dashboard", "price_raise", "price_lower", "approved",
    "missing", "review", "excluded", "processed",
    "redistribute", "new_at_competitors",
    "settings", "trash_bin", "make_automation",
    "smart_automation", "product_factory",
]
