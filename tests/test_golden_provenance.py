# -*- coding: utf-8 -*-
"""لا ادّعاء مصدرٍ غير مؤرَّض في وصف المنتج المُرسَل لسلة.

«بطاقة العطر الموثّقة» كانت تختم نفسها بسطر **«معلومة موثّقة من Fragrantica.»**
بلا أي شرط — بينما مسار الإثراء (``engines/ai_engine.py:fetch_fragrantica_info``)
**لا يكشط Fragrantica أصلاً**: هو نداء Gemini مؤرَّض، وله احتياطيان بلا تأريض
(Gemini بلا grounding ثم OpenRouter) نوتاتهما من ذاكرة النموذج — وتعليق الكود
نفسه يقول «قد تُلفَّق» (:766-775). فكانت البطاقة تَعِد الزبون بتوثيقٍ لم يحدث.

الكود يعرف الفرق أصلاً: ``_grounded`` يُنقَل إلى ``_notes_grounded``
(services/missing_orchestrator.py:331) ويقرؤه حارس البوابة. الإصلاح: سطر التوثيق
يتبع القاعدة نفسها — **المجهول يُسقَط سطره ولا يُلفَّق** — فلا يُطبع إلا بتأريض
صريح. أثقل من حقل مجهول لأنه تلفيق مصدرٍ لا تلفيق قيمة.
"""
import re

from engines.golden_template import build_golden_description

_CLAIM = "موثّقة من Fragrantica"

_NOTES = {
    "اسم المنتج": "عطر ديور سوفاج أو دو تواليت 100 مل للرجال",
    "الماركة": "ديور",
    "top_notes": "برغموت، فلفل",
    "heart_notes": "لافندر",
    "base_notes": "عنبر، مسك",
    "العائلة_العطرية": "أروماتيك",
}


def _text(product: dict) -> str:
    return re.sub("<[^>]+>", " ", build_golden_description(product))


def test_grounded_notes_keep_the_provenance_line():
    """التأريض الحقيقي وحده يشتري الادّعاء — حارس ضد الإفراط في الإسقاط."""
    assert _CLAIM in _text({**_NOTES, "_notes_grounded": True})


def test_ungrounded_notes_drop_the_provenance_line():
    """الاحتياطي بلا تأريض: نوتاته من ذاكرة النموذج ⇒ لا ادّعاء توثيق."""
    txt = _text({**_NOTES, "_notes_grounded": False})
    assert _CLAIM not in txt


def test_unknown_provenance_drops_the_line():
    """غياب العلم ليس إذناً بالادّعاء (المجهول يُسقَط سطره)."""
    assert _CLAIM not in _text(dict(_NOTES))


def test_card_itself_survives_without_the_claim():
    """الإسقاط يطال سطر التوثيق وحده — لا البطاقة ولا النوتات."""
    txt = _text(dict(_NOTES))
    assert "بطاقة العطر الموثّقة" in txt
    assert "برغموت" in txt
    assert "أروماتيك" in txt
