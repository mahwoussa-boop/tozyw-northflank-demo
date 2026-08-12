"""tests/test_copilot_service.py — اختبارات شاملة لخدمة AI Copilot.

تغطي (بدون أي اتصال حقيقي بالإنترنت):
- دقة تقسيم الدفعات (Batching)
- صحة بناء الـ Prompt مع RAG
- تحليل ردود AI (صحيحة / مشوّهة / فاشلة)
- تكامل القواعد الذهبية الثلاث مع الخدمة
- تسجيل القرارات + approve / reject / modify
- معالجة الأخطاء (AI فشل / رد فارغ / JSON غير صالح)
- استدعاء progress_cb
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.db_manager import DatabaseManager
from infrastructure.ai_decisions_repo import (
    AIDecision,
    AIDecisionRepository,
    compute_market_context,
)
from services.ai_service import AIResult, AIService
from services.copilot_service import (
    CopilotService,
    CopilotSuggestion,
    ProductInput,
    build_batch_prompt,
    build_rag_section,
    build_similar_section,
    parse_batch_response,
    _safe_float,
)


# ════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def db(tmp_path):
    """قاعدة بيانات مؤقتة."""
    return DatabaseManager(str(tmp_path / "test_copilot.db"))


@pytest.fixture
def repo(db):
    """مستودع جاهز."""
    r = AIDecisionRepository(db)
    r.ensure_table()
    return r


def _make_mock_ai(response_text: str, success: bool = True, source: str = "OpenRouter"):
    """ينشئ AIService وهمي يُعيد رداً ثابتاً."""
    mock_ai = MagicMock(spec=AIService)
    mock_ai.call.return_value = AIResult(
        success=success,
        response=response_text,
        source=source,
    )
    return mock_ai


def _sample_products(n: int = 3) -> list[ProductInput]:
    """ينشئ n منتج نموذجي."""
    return [
        ProductInput(
            name=f"عطر تست {i + 1}",
            product_id=f"SKU-{i + 1:03d}",
            our_price=300.0 + i * 50,
            competitor_prices=[280.0 + i * 40, 290.0 + i * 45, 285.0 + i * 42],
            competitor_names=[f"منافس_{i}_أ", f"منافس_{i}_ب", f"منافس_{i}_ج"],
            section="price_lower",
        )
        for i in range(n)
    ]


def _good_ai_response(n: int) -> str:
    """رد AI صحيح لـ n منتج."""
    items = []
    for i in range(n):
        items.append({
            "i": i,
            "decision": "lower",
            "suggested_price": 285.0 + i * 10,
            "reason": f"المنافسون يبيعون بأقل → نقترح {285 + i * 10}",
            "confidence": 0.85,
        })
    return json.dumps(items, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════
#  1. اختبارات تقسيم الدفعات (Batching)
# ════════════════════════════════════════════════════════════════════

class TestBatching:
    def test_split_exact_batch_size(self, repo):
        """15 منتج → دفعة واحدة."""
        mock_ai = _make_mock_ai("")
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = _sample_products(15)
        batches = copilot.split_batches(products)
        assert len(batches) == 1
        assert len(batches[0]) == 15

    def test_split_multiple_batches(self, repo):
        """32 منتج ÷ 15 = 3 دفعات (15+15+2)."""
        mock_ai = _make_mock_ai("")
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = _sample_products(32)
        batches = copilot.split_batches(products)
        assert len(batches) == 3
        assert len(batches[0]) == 15
        assert len(batches[1]) == 15
        assert len(batches[2]) == 2

    def test_split_less_than_batch(self, repo):
        """5 منتجات → دفعة واحدة."""
        mock_ai = _make_mock_ai("")
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = _sample_products(5)
        batches = copilot.split_batches(products)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_split_empty(self, repo):
        """0 منتجات → لا دفعات."""
        mock_ai = _make_mock_ai("")
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        batches = copilot.split_batches([])
        assert len(batches) == 0

    def test_max_items_cap(self, repo):
        """200 منتج مع حد 150 → يقطع عند 150."""
        mock_ai = _make_mock_ai("")
        copilot = CopilotService(mock_ai, repo, batch_size=15, max_items=150)
        products = _sample_products(200)
        batches = copilot.split_batches(products)
        total = sum(len(b) for b in batches)
        assert total == 150

    def test_actual_ai_calls_match_batches(self, repo):
        """يتأكد أن عدد نداءات AI = عدد الدفعات."""
        mock_ai = _make_mock_ai(_good_ai_response(5))
        copilot = CopilotService(mock_ai, repo, batch_size=5)
        products = _sample_products(12)
        copilot.analyze(products)
        # 12 ÷ 5 = 3 نداءات
        assert mock_ai.call.call_count == 3


# ════════════════════════════════════════════════════════════════════
#  2. اختبارات بناء الـ Prompt
# ════════════════════════════════════════════════════════════════════

class TestPromptBuilding:
    def test_prompt_contains_product_info(self):
        """الـ prompt يحتوي اسم المنتج وسعرنا."""
        products = _sample_products(2)
        prompt = build_batch_prompt(products, [], {})
        assert "عطر تست 1" in prompt
        assert "300" in prompt  # our_price

    def test_prompt_contains_market_context(self):
        """الـ prompt يحتوي متوسط السوق وعدد المنافسين."""
        products = [ProductInput(
            name="عطر فاخر",
            our_price=400,
            competitor_prices=[350, 360, 370],
        )]
        prompt = build_batch_prompt(products, [], {})
        assert "متوسط السوق" in prompt
        assert "3" in prompt  # عدد المنافسين

    def test_prompt_contains_consensus(self):
        """الـ prompt ينبّه على إجماع سوقي عند وجوده."""
        products = [ProductInput(
            name="عطر شعبي",
            our_price=300,
            competitor_prices=[250, 250, 250, 400],
        )]
        prompt = build_batch_prompt(products, [], {})
        assert "إجماع" in prompt

    def test_prompt_with_rag_examples(self):
        """حقن RAG: أمثلة تعلّم سابقة تظهر في الـ prompt."""
        examples = [
            {
                "product_name": "عطر سابق",
                "our_price": 300,
                "proposed_price": 280,
                "user_action": "approved",
                "ai_decision": "lower",
                "user_price": 280,
                "safety_flag": "",
            },
            {
                "product_name": "عطر معدّل",
                "our_price": 400,
                "proposed_price": 350,
                "user_action": "modified",
                "ai_decision": "lower",
                "user_price": 370,
                "safety_flag": "max_drop",
            },
        ]
        prompt = build_batch_prompt(_sample_products(1), examples, {})
        assert "عطر سابق" in prompt
        assert "وافق" in prompt
        assert "عدّل" in prompt
        assert "370" in prompt  # user_price for modified

    def test_prompt_with_similar_decisions(self):
        """حقن RAG سياقي: قرارات مشابهة لمنتج محدد."""
        similar_map = {
            "عطر تست 1": [
                {
                    "our_price": 290,
                    "proposed_price": 270,
                    "user_action": "rejected",
                    "user_price": 0,
                },
            ],
        }
        prompt = build_batch_prompt(_sample_products(1), [], similar_map)
        assert "قرارات سابقة" in prompt
        assert "290" in prompt

    def test_prompt_json_instruction(self):
        """الـ prompt ينتهي بتعليمات JSON واضحة."""
        prompt = build_batch_prompt(_sample_products(3), [], {})
        assert "JSON array" in prompt
        assert "3" in prompt


class TestRAGSections:
    def test_empty_rag(self):
        assert build_rag_section([]) == ""

    def test_approved_example(self):
        result = build_rag_section([{
            "product_name": "عطر", "our_price": 300,
            "proposed_price": 280, "user_action": "approved",
            "ai_decision": "lower", "user_price": 280, "safety_flag": "",
        }])
        assert "✅" in result
        assert "وافق" in result

    def test_rejected_example(self):
        result = build_rag_section([{
            "product_name": "عطر", "our_price": 300,
            "proposed_price": 200, "user_action": "rejected",
            "ai_decision": "lower", "user_price": 0,
            "safety_flag": "", "ai_reason": "غالي جداً",
        }])
        assert "❌" in result
        assert "رفض" in result

    def test_modified_with_flag(self):
        result = build_rag_section([{
            "product_name": "عطر", "our_price": 300,
            "proposed_price": 200, "user_action": "modified",
            "ai_decision": "lower", "user_price": 260,
            "safety_flag": "max_drop",
        }])
        assert "✏️" in result
        assert "260" in result
        assert "max_drop" in result

    def test_empty_similar(self):
        assert build_similar_section([]) == ""


# ════════════════════════════════════════════════════════════════════
#  3. اختبارات تحليل ردود AI
# ════════════════════════════════════════════════════════════════════

class TestResponseParsing:
    def test_valid_response(self):
        """رد JSON صحيح → تحليل ناجح."""
        resp = json.dumps([
            {"i": 0, "decision": "lower", "suggested_price": 280, "reason": "أقل", "confidence": 0.9},
            {"i": 1, "decision": "hold", "suggested_price": 300, "reason": "مناسب", "confidence": 0.95},
        ])
        results = parse_batch_response(resp, 2)
        assert len(results) == 2
        assert results[0]["decision"] == "lower"
        assert results[0]["suggested_price"] == 280
        assert results[1]["decision"] == "hold"

    def test_response_with_code_fences(self):
        """رد JSON ملفوف بأسوار ``` → يُحلّل بنجاح."""
        inner = json.dumps([{"i": 0, "decision": "raise", "suggested_price": 350,
                             "reason": "نحن أقل", "confidence": 0.8}])
        resp = f"```json\n{inner}\n```"
        results = parse_batch_response(resp, 1)
        assert results[0]["decision"] == "raise"
        assert results[0]["suggested_price"] == 350

    def test_empty_response_returns_defaults(self):
        """رد فارغ → كل القرارات review."""
        results = parse_batch_response("", 3)
        assert len(results) == 3
        assert all(r["decision"] == "review" for r in results)

    def test_garbage_response(self):
        """رد عشوائي غير JSON → review افتراضي."""
        results = parse_batch_response("أهلاً! ما عندي إجابة", 2)
        assert len(results) == 2
        assert all(r["decision"] == "review" for r in results)

    def test_partial_response(self):
        """رد جزئي (منتج واحد من 3) → الباقي review."""
        resp = json.dumps([{"i": 1, "decision": "lower", "suggested_price": 260,
                            "reason": "خفض", "confidence": 0.7}])
        results = parse_batch_response(resp, 3)
        assert results[0]["decision"] == "review"  # افتراضي
        assert results[1]["decision"] == "lower"    # من AI
        assert results[2]["decision"] == "review"   # افتراضي

    def test_out_of_range_index_ignored(self):
        """فهرس خارج المدى → يُتجاهل."""
        resp = json.dumps([{"i": 99, "decision": "lower", "suggested_price": 100,
                            "reason": "?", "confidence": 0.5}])
        results = parse_batch_response(resp, 2)
        assert all(r["decision"] == "review" for r in results)

    def test_confidence_clamped(self):
        """ثقة > 1.0 أو < 0.0 → تُقيّد."""
        resp = json.dumps([{"i": 0, "decision": "hold", "suggested_price": 300,
                            "reason": "ok", "confidence": 1.5}])
        results = parse_batch_response(resp, 1)
        assert results[0]["confidence"] == 1.0


# ════════════════════════════════════════════════════════════════════
#  4. اختبارات التكامل (CopilotService.analyze)
# ════════════════════════════════════════════════════════════════════

class TestAnalyze:
    def test_successful_analysis(self, repo):
        """تحليل ناجح: 3 منتجات → 3 اقتراحات مسجّلة."""
        products = _sample_products(3)
        mock_ai = _make_mock_ai(_good_ai_response(3))
        copilot = CopilotService(mock_ai, repo, batch_size=15)

        suggestions = copilot.analyze(products)
        assert len(suggestions) == 3
        for s in suggestions:
            assert isinstance(s, CopilotSuggestion)
            assert s.db_id > 0
            assert s.decision.user_action == "pending"

        # التحقق من قاعدة البيانات
        pending = repo.get_pending()
        assert len(pending) == 3

    def test_ai_failure_returns_review(self, repo):
        """فشل AI → كل القرارات review (لا خسارة بيانات)."""
        products = _sample_products(2)
        mock_ai = _make_mock_ai("", success=False, source="failed")
        copilot = CopilotService(mock_ai, repo, batch_size=15)

        suggestions = copilot.analyze(products)
        assert len(suggestions) == 2
        for s in suggestions:
            assert s.decision.ai_decision == "review"
            assert s.decision.ai_model == "failed"

    def test_guardrails_applied_on_steep_drop(self, repo):
        """هبوط حاد (25%) → الحارس يوقف ويعلّم."""
        products = [ProductInput(
            name="عطر غالي",
            our_price=400,
            competitor_prices=[300, 310, 320],
            section="price_lower",
        )]
        # AI يقترح 300 (هبوط 25%) → الحارس يرفعه لـ 340 (85% من 400)
        ai_resp = json.dumps([{
            "i": 0, "decision": "lower", "suggested_price": 300,
            "reason": "المنافسون بـ300", "confidence": 0.9,
        }])
        mock_ai = _make_mock_ai(ai_resp)
        copilot = CopilotService(mock_ai, repo, batch_size=15, max_drop_pct=0.15)

        suggestions = copilot.analyze(products)
        s = suggestions[0]
        assert s.safety.is_safe is False
        assert s.decision.safety_flag != ""
        assert s.decision.ai_decision == "review"  # الحارس غيّره
        assert s.decision.proposed_price == 340.0   # 400 * 0.85

    def test_guardrails_consensus_accepted(self, repo):
        """إجماع سوقي (4 منافسين بنفس السعر) + هبوط 10% → يُقبل."""
        products = [ProductInput(
            name="عطر شائع",
            our_price=300,
            competitor_prices=[270, 270, 270, 270],
            section="price_lower",
        )]
        ai_resp = json.dumps([{
            "i": 0, "decision": "lower", "suggested_price": 270,
            "reason": "إجماع سوقي", "confidence": 0.95,
        }])
        mock_ai = _make_mock_ai(ai_resp)
        copilot = CopilotService(mock_ai, repo, batch_size=15)

        suggestions = copilot.analyze(products)
        s = suggestions[0]
        # الإجماع يتجاوز لأن الهبوط < 15%
        assert s.decision.proposed_price == 270.0
        assert s.safety.is_safe is True

    def test_multi_batch_analysis(self, repo):
        """20 منتج ÷ 5 = 4 دفعات → 4 نداءات AI."""
        products = _sample_products(20)

        def side_effect(prompt, system, **kwargs):
            # نحسب عدد المنتجات في الـ prompt
            count = prompt.count("المنتج: «")
            return AIResult(True, _good_ai_response(count), "OpenRouter")

        mock_ai = MagicMock(spec=AIService)
        mock_ai.call.side_effect = side_effect

        copilot = CopilotService(mock_ai, repo, batch_size=5)
        suggestions = copilot.analyze(products)

        assert len(suggestions) == 20
        assert mock_ai.call.call_count == 4

    def test_progress_callback(self, repo):
        """progress_cb يُستدعى بعد كل دفعة."""
        products = _sample_products(10)
        mock_ai = _make_mock_ai(_good_ai_response(5))
        copilot = CopilotService(mock_ai, repo, batch_size=5)

        progress_calls = []
        copilot.analyze(products, progress_cb=lambda c, t: progress_calls.append((c, t)))

        assert len(progress_calls) == 2  # 2 دفعات
        assert progress_calls[0] == (5, 10)
        assert progress_calls[1] == (10, 10)

    def test_rag_examples_fetched_once(self, repo):
        """أمثلة RAG تُجلب مرة واحدة (لا لكل دفعة)."""
        # أضف قرار سابق
        d = AIDecision(product_name="عطر سابق", our_price=300, proposed_price=280)
        rid = repo.log_decision(d)
        repo.resolve(rid, "approved", user_price=280)

        products = _sample_products(10)
        mock_ai = _make_mock_ai(_good_ai_response(5))
        copilot = CopilotService(mock_ai, repo, batch_size=5)

        copilot.analyze(products)

        # كل نداءات AI يجب أن تحتوي "عطر سابق" (RAG)
        for call_args in mock_ai.call.call_args_list:
            prompt = call_args[0][0]  # أول argument
            assert "عطر سابق" in prompt


# ════════════════════════════════════════════════════════════════════
#  5. اختبارات إجراءات المستخدم
# ════════════════════════════════════════════════════════════════════

class TestUserActions:
    def _analyze_one(self, repo) -> CopilotSuggestion:
        """مساعد: يحلّل منتجاً واحداً ويُعيد الاقتراح."""
        products = _sample_products(1)
        mock_ai = _make_mock_ai(_good_ai_response(1))
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        return copilot.analyze(products)[0], copilot

    def test_approve(self, repo):
        s, copilot = self._analyze_one(repo)
        copilot.approve(s.db_id, price=285)
        pending = repo.get_pending()
        assert len(pending) == 0
        # يظهر في أمثلة التعلّم
        examples = repo.get_learning_examples()
        assert len(examples) == 1
        assert examples[0]["user_action"] == "approved"

    def test_reject(self, repo):
        s, copilot = self._analyze_one(repo)
        copilot.reject(s.db_id, note="لا أوافق")
        pending = repo.get_pending()
        assert len(pending) == 0
        examples = repo.get_learning_examples()
        assert examples[0]["user_action"] == "rejected"

    def test_modify(self, repo):
        s, copilot = self._analyze_one(repo)
        copilot.modify(s.db_id, 295, note="عدّلت السعر")
        examples = repo.get_learning_examples()
        assert examples[0]["user_action"] == "modified"
        assert examples[0]["user_price"] == 295

    def test_stats_after_actions(self, repo):
        products = _sample_products(4)
        mock_ai = _make_mock_ai(_good_ai_response(4))
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        suggestions = copilot.analyze(products)

        copilot.approve(suggestions[0].db_id)
        copilot.reject(suggestions[1].db_id)
        copilot.modify(suggestions[2].db_id, 290)
        # suggestions[3] يبقى pending

        stats = copilot.get_stats()
        assert stats["approved"] == 1
        assert stats["rejected"] == 1
        assert stats["modified"] == 1
        assert stats["pending"] == 1
        assert stats["total"] == 4


# ════════════════════════════════════════════════════════════════════
#  6. اختبارات معالجة الأخطاء
# ════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    def test_ai_exception_handled(self, repo):
        """AI يرمي استثناء → يُلتقط ويُعاد كـ review."""
        mock_ai = MagicMock(spec=AIService)
        mock_ai.call.side_effect = Exception("Network timeout")

        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = _sample_products(2)

        # لا ينهار — يُعيد review
        suggestions = copilot.analyze(products)
        assert len(suggestions) == 2
        for s in suggestions:
            assert s.decision.ai_decision == "review"

    def test_malformed_json_response(self, repo):
        """رد AI مشوّه (JSON غير صالح) → review افتراضي."""
        mock_ai = _make_mock_ai('{"broken: json]}}}')
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = _sample_products(2)

        suggestions = copilot.analyze(products)
        assert len(suggestions) == 2
        for s in suggestions:
            assert s.decision.ai_decision == "review"

    def test_zero_suggested_price_uses_our_price(self, repo):
        """AI يقترح 0 → نستخدم سعرنا الحالي."""
        resp = json.dumps([{"i": 0, "decision": "hold", "suggested_price": 0,
                            "reason": "لا تغيير", "confidence": 0.8}])
        mock_ai = _make_mock_ai(resp)
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = [ProductInput(name="عطر", our_price=350)]

        suggestions = copilot.analyze(products)
        assert suggestions[0].decision.proposed_price == 350.0

    def test_negative_price_from_ai(self, repo):
        """AI يقترح سعراً سالباً → يستخدم سعرنا."""
        resp = json.dumps([{"i": 0, "decision": "lower", "suggested_price": -50,
                            "reason": "خطأ", "confidence": 0.1}])
        mock_ai = _make_mock_ai(resp)
        copilot = CopilotService(mock_ai, repo, batch_size=15)
        products = [ProductInput(name="عطر", our_price=300)]

        suggestions = copilot.analyze(products)
        # السعر السالب يمر كـ -50 ثم الحارس يوقفه
        s = suggestions[0]
        assert s.decision.proposed_price >= 0  # لا سعر سالب نهائي


# ════════════════════════════════════════════════════════════════════
#  7. اختبارات _safe_float
# ════════════════════════════════════════════════════════════════════

class TestSafeFloat:
    def test_normal(self):
        assert _safe_float(42.5) == 42.5

    def test_string_number(self):
        assert _safe_float("300") == 300.0

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_nan_string(self):
        assert _safe_float("nan") == 0.0

    def test_empty_string(self):
        assert _safe_float("") == 0.0

    def test_garbage(self):
        assert _safe_float("abc") == 0.0

    def test_default(self):
        assert _safe_float(None, 99.9) == 99.9


# ════════════════════════════════════════════════════════════════════
#  8. اختبار السيناريو الشامل (End-to-End)
# ════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_workflow(self, repo):
        """سيناريو كامل: تحليل → موافقة/رفض/تعديل → التعلّم يؤثر على الدفعة التالية."""

        # === الدفعة الأولى (لا أمثلة سابقة) ===
        products_1 = [
            ProductInput(name="عطر ديور", our_price=350,
                         competitor_prices=[320, 330, 325]),
            ProductInput(name="عطر شانيل", our_price=400,
                         competitor_prices=[280, 290, 285]),  # هبوط حاد
        ]
        ai_resp_1 = json.dumps([
            {"i": 0, "decision": "lower", "suggested_price": 325, "reason": "مطابقة", "confidence": 0.9},
            {"i": 1, "decision": "lower", "suggested_price": 285, "reason": "خفض حاد", "confidence": 0.7},
        ])
        mock_ai = _make_mock_ai(ai_resp_1)
        copilot = CopilotService(mock_ai, repo, batch_size=15, max_drop_pct=0.15)

        suggestions_1 = copilot.analyze(products_1)
        assert len(suggestions_1) == 2

        # ديور: هبوط ~7% → آمن
        assert suggestions_1[0].safety.is_safe is True
        assert suggestions_1[0].decision.proposed_price == 325.0

        # شانيل: هبوط ~29% → الحارس يوقف عند 340 (400*0.85)
        assert suggestions_1[1].safety.is_safe is False
        assert suggestions_1[1].decision.proposed_price == 340.0

        # قرارات المستخدم
        copilot.approve(suggestions_1[0].db_id, price=325)
        copilot.modify(suggestions_1[1].db_id, 360, note="نزلت شوية بس")

        # === الدفعة الثانية (مع RAG) ===
        products_2 = [
            ProductInput(name="عطر جيفنشي", our_price=380,
                         competitor_prices=[350, 355, 345]),
        ]
        ai_resp_2 = json.dumps([
            {"i": 0, "decision": "lower", "suggested_price": 350, "reason": "قريب", "confidence": 0.85},
        ])

        # نحتاج mock جديد لأن call_count يتراكم
        mock_ai_2 = _make_mock_ai(ai_resp_2)
        copilot_2 = CopilotService(mock_ai_2, repo, batch_size=15)

        suggestions_2 = copilot_2.analyze(products_2)
        assert len(suggestions_2) == 1

        # التحقق أن RAG يُحقن (الـ prompt يحوي قرارات سابقة)
        call_args = mock_ai_2.call.call_args
        prompt = call_args[0][0]
        assert "عطر ديور" in prompt or "عطر شانيل" in prompt  # RAG يعمل

        # الإحصاءات النهائية
        stats = copilot_2.get_stats()
        assert stats["total"] == 3
        assert stats["approved"] == 1
        assert stats["modified"] == 1
        assert stats["pending"] == 1
