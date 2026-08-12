# -*- coding: utf-8 -*-
"""انحدار أمني: النسخة المحمولة يجب ألا تحوي أسراراً حيّة.

خلفية العطب: `scripts/make_portable_zip.py` كان يمشي الشجرة بـ`os.walk` ويحزم
كل ملف لا يطابق قوائم الاستثناء. لم يكن `.env` ولا `.streamlit/secrets.toml`
مستثنيين، فكان كل zip منقول يسرّب توكن سلة (بصلاحية كتابة المتجر الحيّ) ومفاتيح
Gemini/OpenRouter/Algolia. هذا الاختبار يثبّت الاستثناء ويمنع عودة العطب.
"""
import os

from scripts.make_portable_zip import _is_secret, _skip


def test_live_secrets_are_excluded():
    # ملفات الأسرار الحيّة يجب أن تُستثنى دائماً.
    assert _is_secret(".env") is True
    assert _is_secret(".env.local") is True
    assert _is_secret("secrets.toml") is True
    assert _skip(".env") is True
    assert _skip(os.path.join(".streamlit", "secrets.toml")) is True


def test_safe_templates_and_config_are_kept():
    # القوالب وإعداد Streamlit آمنة ومفيدة على الجهاز الآخر — يجب ألّا تُستثنى.
    assert _is_secret(".env.example") is False
    assert _is_secret("secrets.toml.example") is False
    assert _skip(".env.example") is False
    assert _skip(os.path.join(".streamlit", "secrets.toml.example")) is False
    assert _skip(os.path.join(".streamlit", "config.toml")) is False


def test_secret_names_as_they_appear_in_walk():
    # حراسة صريحة: بالاسم المجرّد كما يراه os.walk، لا يمرّ أي سرّ عبر _skip.
    for name in (".env", "secrets.toml"):
        assert _skip(name) is True, f"تسريب محتمل: {name} لم يُستثنَ"
