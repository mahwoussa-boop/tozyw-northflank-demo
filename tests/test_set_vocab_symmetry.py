# -*- coding: utf-8 -*-
"""انحدار مطابقة (v37): تناظر مفردات كشف المجموعة بين _is_set_norm و classify_product.

خلفية العطب: حارس المجموعات (engine.py ~1961) يستعمل `_is_set_norm` على الطرفين،
لكن مفرداته كانت أضيق من `classify_product`: يكشف مجموعة/طقم/gift set/gift box فقط،
ويفوّت coffret/gift/سيت/هدية. النتيجة: توأم مجموعة↔مجموعة شرعي يُرفض حين يستعمل أحد
الطرفين مفردةً من القائمة الضيقة والآخر من المفقودة (مثال: «Coffret» لدينا مقابل
«مجموعة» عند المنافس) → `_our_is_set != _comp_is_set` → رفض خاطئ. هذا الاختبار يثبّت
المواءمة ويمنع عودة الانجراف.
"""
from engines.engine import _is_set_norm, classify_product, normalize


def test_previously_missed_set_vocab_now_detected():
    # هذه كانت تُصنَّف set في classify لكن _is_set_norm يفوّتها (False) قبل الإصلاح.
    for name in ("Dior Coffret", "Gift Pouch", "طقم هدية", "عطر سيت"):
        n = normalize(name)
        assert classify_product(n) == "set", f"classify لا يراه set: {name}"
        assert _is_set_norm(n) is True, f"حارس المجموعة يفوّته: {name}"


def test_original_set_vocab_still_detected():
    for name in ("مجموعة عطور رجالية", "gift box", "gift set", "طقم"):
        assert _is_set_norm(normalize(name)) is True, f"انكسر كشف قديم: {name}"


def test_plain_perfume_is_not_flagged_as_set():
    # لا false-positive: عطر فردي يجب ألّا يُعدّ مجموعة (لا نرفض توأمه الشرعي).
    for name in ("Bleu de Chanel EDP", "عطر رجالي فخم", "Sauvage Eau de Parfum"):
        assert _is_set_norm(normalize(name)) is False, f"false-positive مجموعة: {name}"
