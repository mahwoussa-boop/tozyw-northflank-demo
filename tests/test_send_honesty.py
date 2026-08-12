"""tests/test_send_honesty.py — صدق حالة الإرسال (دوال نقيّة، بلا POST/شبكة).

c1: build_new_send_outcome — التخطّي ليس فشلاً، والفشل يذكر أول سبب.
c2: parse_webhook_result — أُنشئ/أُرسل-تحقّق/فشل حسب جسم الرد.
"""
from utils.make_helper import build_new_send_outcome


# ── c1: صدق رسالة الإرسال ─────────────────────────────────────────────────
def test_all_duplicates_is_info_not_failure():
    out = build_new_send_outcome(sent=0, skipped=1, failed=0, total=1)
    assert out["success"] is True
    assert out["level"] == "info"
    assert "أُرسل سابقاً" in out["message"]
    assert "فشل" not in out["message"]          # لا يُسمّى فشلاً


def test_real_failure_reports_first_cause():
    err = {"message": "❌ HTTP 400: Invalid product", "status_code": 400}
    out = build_new_send_outcome(sent=0, skipped=0, failed=1, total=1, first_error=err)
    assert out["success"] is False
    assert out["level"] == "error"
    assert "400" in out["message"] and "أول سبب" in out["message"]
    assert out["status_code"] == 400


def test_failure_with_skips_still_failure():
    # sent==0 لكن يوجد فشل فعليّ (مع تخطٍّ) ⇒ فشل لا معلومة.
    out = build_new_send_outcome(sent=0, skipped=2, failed=1, total=3,
                                 first_error={"message": "❌ timeout", "status_code": 0})
    assert out["success"] is False and out["level"] == "error"


def test_partial_success_counts_and_notes():
    out = build_new_send_outcome(sent=2, skipped=1, failed=1, total=4)
    assert out["success"] is True and out["sent"] == 2
    assert "تم إرسال 2" in out["message"]
    assert "تخطي 1" in out["message"] and "فشل 1" in out["message"]


def test_full_success_clean():
    out = build_new_send_outcome(sent=3, skipped=0, failed=0, total=3)
    assert out["success"] is True and out["status_code"] == 200
    assert "تم إرسال 3" in out["message"]


# ── c2: صدق تحليل ردّ الويبهوك (متوافق مع إصلاح Make القادم) ───────────────
from utils.make_helper import parse_webhook_result


def test_parse_confirmed_created_with_id():
    out = parse_webhook_result(True, 200, '{"ok": true, "product_id": 12345}')
    assert out["state"] == "confirmed" and out["product_id"] == 12345
    assert "أُنشئ في سلة" in out["message"] and "12345" in out["message"]


def test_parse_ok_false_is_failure_with_error():
    out = parse_webhook_result(True, 200, '{"ok": false, "error": "category rejected"}')
    assert out["state"] == "failed"
    assert "category rejected" in out["message"]


def test_parse_plain_accepted_is_accepted_not_confirmed():
    # الحالة الراهنة: Make يردّ 202 Accepted فوراً قبل اكتمال السيناريو.
    out = parse_webhook_result(True, 202, "Accepted")
    assert out["state"] == "accepted"
    assert "تحقّق" in out["message"] and "أُنشئ" not in out["message"]


def test_parse_non_2xx_is_failure_with_body():
    out = parse_webhook_result(False, 400, "Invalid product: missing name")
    assert out["state"] == "failed"
    assert "400" in out["message"] and "Invalid product" in out["message"]


def test_parse_empty_2xx_is_accepted():
    assert parse_webhook_result(True, 200, "")["state"] == "accepted"
