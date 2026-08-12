"""tests/test_make_send_reason.py — تسريب سبب فشل الإرسال (بدل «فشل N» الغامض).

عند رفض الإرسال (صورة/وصف/ويبهوك) كان send_batch_smart يبتلع السبب ويُرجع رسالة
عامّة، فيرى المستخدم «فشل» بلا سبب. الآن يُسرَّب السبب في الرسالة وفي fail_reason.
"""
import utils.make_helper as mh


def test_send_batch_surfaces_failure_reason(monkeypatch) -> None:
    def fake_new(batch):
        return {"success": False, "sent": 0,
                "message": "❌ لا توجد منتجات صالحة — رفض 1 لغياب صورة حقيقية"}
    monkeypatch.setattr(mh, "send_new_products", fake_new)

    out = mh.send_batch_smart(
        [{"name": "عطر س", "منتج_المنافس": "عطر س"}],
        batch_type="new", max_retries=1,
    )
    assert out["success"] is False
    assert out["sent"] == 0
    assert "السبب" in out["message"]
    assert "صورة حقيقية" in out["message"]
    assert out["fail_reason"] == "❌ لا توجد منتجات صالحة — رفض 1 لغياب صورة حقيقية"


def test_send_batch_success_has_no_reason(monkeypatch) -> None:
    monkeypatch.setattr(
        mh, "send_new_products",
        lambda batch: {"success": True, "sent": len(batch), "message": "ok"},
    )
    out = mh.send_batch_smart([{"name": "عطر"}], batch_type="new", max_retries=1)
    assert out["success"] is True and out["sent"] == 1
    assert out.get("fail_reason", "") == ""
    assert "السبب" not in out["message"]
