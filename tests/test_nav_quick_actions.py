"""tests/test_nav_quick_actions.py — أزرار «إجراءات سريعة» تنقل بلا StreamlitAPIException.

كانت أزرار ``_render_quick_actions`` تُسند ``st.session_state["nav_page"]`` مباشرةً
داخل ``if st.button(...)`` — بعد أن أنشأ الشريطُ الجانبي عنصرَ الراديو بنفس المفتاح
``nav_page`` ⇒ ``StreamlitAPIException`` (لا يمكن تعديل قيمة عنصر بعد إنشائه). الإصلاح
الرسمي: الإسناد عبر ``on_click`` (يعمل قبل إعادة التشغيل فيُسمح له بتعديل قيمة العنصر).

حالة صناعية صغيرة (لا لقطة 282MB) كي يبقى الاختبار خفيفاً.
"""
import pandas as pd
from streamlit.testing.v1 import AppTest

from ui.state_manager import AppState


def _boot_with_small_state():
    at = AppTest.from_file("app.py", default_timeout=60)
    s = AppState()
    s.sections = {"price_raise": pd.DataFrame(
        {"المنتج": ["منتج"], "السعر": [100.0], "سعر_المنافس": [80.0]})}
    at.session_state["_app_state_v2"] = s  # يظهر «إجراءات سريعة» (has_data)
    at.run()
    return at


def test_quick_action_raise_navigates_without_exception():
    at = _boot_with_small_state()
    at.button(key="qa_raise").click().run()
    assert not at.exception, list(at.exception)  # كان StreamlitAPIException
    assert at.session_state["nav_page"] == "🔴 سعر أعلى"


def test_quick_action_lower_navigates_without_exception():
    at = _boot_with_small_state()
    at.button(key="qa_lower").click().run()
    assert not at.exception, list(at.exception)
    assert at.session_state["nav_page"] == "🟢 سعر أقل"
