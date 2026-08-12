"""tests/test_processed_images.py — إظهار صور «تمت المعالجة».

log_action يقبل ``image`` ويخزّنه في row_data["image"]؛ بطاقة processed تعرضه
كـ<img>. غياب الصورة ⇒ الأيقونة الافتراضية (لا كسر). row_image_url يستخرج الرابط
من مفاتيح الإرسال.
"""
from ui.pages.processed import _KIND_ICON, _build_card_html
from ui.state_manager import AppState, row_image_url

_IMG = "https://cdn.example.com/p/123.jpg"


def _first_entry(state):
    return state.processed_log[0]


def test_log_action_with_image_renders_img_tag():
    state = AppState()
    state.log_action(
        key="k1", name="عطر تجريبي", action="تجهيز وإرسال (مفقود)",
        detail="…", kind="missing", image=_IMG,
    )
    entry = _first_entry(state)
    assert entry["row_data"]["image"] == _IMG
    html = _build_card_html(entry)
    assert "<img" in html
    assert _IMG in html


def test_log_action_without_image_uses_default_icon():
    state = AppState()
    state.log_action(
        key="k2", name="عطر بلا صورة", action="تجهيز وإرسال (مفقود)",
        detail="…", kind="missing",
    )
    entry = _first_entry(state)
    assert "row_data" not in entry  # لا بيانات بصرية ⇒ لا row_data
    html = _build_card_html(entry)
    assert "<img" not in html  # لا صورة منتج ⇒ لم تُبنَ وسم img
    assert _KIND_ICON.get("missing", "✅") in html  # أيقونة القسم الافتراضية بدلاً منها


def test_log_action_empty_image_falls_back_to_icon():
    state = AppState()
    state.log_action(key="k3", name="x", action="a", kind="missing", image="")
    html = _build_card_html(_first_entry(state))
    assert "<img" not in html


def test_row_data_image_preserved_alongside_image_param():
    # لو مُرِّر row_data يحمل بيانات أخرى، تبقى + تُضاف الصورة.
    state = AppState()
    state.log_action(
        key="k4", name="x", action="a", kind="missing",
        row_data={"brand": "ديور"}, image=_IMG,
    )
    rd = _first_entry(state)["row_data"]
    assert rd["brand"] == "ديور" and rd["image"] == _IMG


def test_row_image_url_extraction():
    assert row_image_url({"صورة_المنافس": _IMG}) == _IMG
    assert row_image_url({"image_url": _IMG}) == _IMG
    assert row_image_url({"صورة المنتج": _IMG}) == _IMG
    # أولوية: صورة_المنافس أولاً
    assert row_image_url({"صورة_المنافس": _IMG, "image_url": "other"}) == _IMG
    # قيم غير صالحة ⇒ ""
    assert row_image_url({"صورة_المنافس": "nan"}) == ""
    assert row_image_url({"صورة_المنافس": None}) == ""
    assert row_image_url({}) == ""
    assert row_image_url(None) == ""
