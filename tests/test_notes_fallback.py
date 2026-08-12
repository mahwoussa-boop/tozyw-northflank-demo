"""tests/test_notes_fallback.py — احتياطي OpenRouter لجلب نفحات العطر.

fetch_fragrantica_info كان يعتمد Gemini فقط (مؤرَّض ثم ذاكرة). أُضيف احتياطي
OpenRouter (مفتاح متوفّر) لرفع تغطية النفحات للعطور النيش (مثل ارماف عود) —
مع بقاء ``_grounded=False`` للمصادر غير المؤرَّضة كي يحجزها حارس البوابة للمراجعة
(لا اختلاق صامت). اختبارات وحدة بمحاكاة النداءات (بلا شبكة).
"""
import engines.ai_engine as ai


def test_openrouter_fallback_when_gemini_empty(monkeypatch):
    monkeypatch.setattr(ai, "_call_gemini", lambda *a, **k: None)  # كلا نداءي Gemini يفشلان
    monkeypatch.setattr(
        ai, "_call_openrouter",
        lambda *a, **k: '{"top_notes":["البرغموت"],"middle_notes":["الورد"],"base_notes":["العنبر"]}',
    )
    r = ai.fetch_fragrantica_info("ارماف اتر ماجيكال عود")
    assert r["success"] is True
    assert r["_grounded"] is False           # مصدر غير مؤرَّض ⇒ للمراجعة
    assert r["top_notes"] == ["البرغموت"]
    assert r["base_notes"] == ["العنبر"]


def test_all_sources_fail_returns_false(monkeypatch):
    monkeypatch.setattr(ai, "_call_gemini", lambda *a, **k: None)
    monkeypatch.setattr(ai, "_call_openrouter", lambda *a, **k: None)
    assert ai.fetch_fragrantica_info("منتج مجهول تماماً") == {"success": False}


def test_grounded_gemini_wins_and_skips_openrouter(monkeypatch):
    monkeypatch.setattr(
        ai, "_call_gemini",
        lambda p, system="", grounding=False, **k: (
            '{"top_notes":["مؤرَّض"]}' if grounding else None
        ),
    )
    calls = []
    monkeypatch.setattr(ai, "_call_openrouter", lambda *a, **k: calls.append(1) or None)
    r = ai.fetch_fragrantica_info("ديور سوفاج")
    assert r["success"] is True and r["_grounded"] is True
    assert r["top_notes"] == ["مؤرَّض"]
    assert calls == []                       # الاحتياطي لم يُستدعَ (لا كلفة زائدة)


def test_nongrounded_gemini_used_before_openrouter(monkeypatch):
    # التأريض يفشل، لكن Gemini بلا تأريض ينجح ⇒ لا OpenRouter، و_grounded=False.
    monkeypatch.setattr(
        ai, "_call_gemini",
        lambda p, system="", grounding=False, **k: (
            None if grounding else '{"top_notes":["ذاكرة"]}'
        ),
    )
    calls = []
    monkeypatch.setattr(ai, "_call_openrouter", lambda *a, **k: calls.append(1) or None)
    r = ai.fetch_fragrantica_info("عطر")
    assert r["success"] is True and r["_grounded"] is False
    assert calls == []
