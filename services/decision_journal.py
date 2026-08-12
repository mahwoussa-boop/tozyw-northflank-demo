"""services/decision_journal.py — دفتر القرارات الصامت (كتابة فقط في events).

يلتقط أفعال المستخدم في جدول ``events`` (pricing_v18.db) عبر ``log_event``.
لا واجهة، لا UPDATE/DELETE، ولا يكسر الإرسال أبداً (كل نداء مطوَّق — القاعدة 5).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

_logger = logging.getLogger(__name__)

_EVENT_TYPES = frozenset({"send_price", "send_new", "ignore", "recover", "band_override"})
_ACTION_AR = {
    "send_price": "إرسال تحديث سعر", "send_new": "إرسال منتج مفقود",
    "ignore": "تجاهل/حذف ناعم", "recover": "استرجاع من المحذوفات",
    "band_override": "تجاوز مسجّل لسياج نطاق السعر",
}


def record_decision(
    event_type: str, product_name: str, page: str, *,
    suggested: Optional[float] = None, chosen: Optional[float] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """يُدرج صفّ قرار واحداً في ``events`` (كتابة فقط). لا يرفع استثناءً أبداً."""
    try:
        if event_type not in _EVENT_TYPES:
            _logger.warning("decision_journal: نوع حدث مجهول %r — تخطّي", event_type)
            return
        details: dict[str, Any] = {"suggested": suggested, "chosen": chosen, "actor": "shared"}
        if extra:
            details.update(extra)
        from utils.db_manager import log_event
        log_event(page=str(page or ""), event_type=event_type,
                  product_name=str(product_name or ""),
                  action=_ACTION_AR.get(event_type, event_type),
                  details=json.dumps(details, ensure_ascii=False))
    except Exception as exc:  # القاعدة 5: لا يكسر الإرسال مهما حدث
        _logger.warning("decision_journal: تعذّر التسجيل: %s", exc)
