"""infrastructure/ai_decisions_repo.py — مستودع قرارات الذكاء الاصطناعي.

يدير جدول ``ai_decisions_log`` الذي يخزّن كل اقتراح سعري من AI مع
قرار المستخدم (approved/rejected/modified) لبناء ذاكرة تعلّم مستمر.

═══════════════════════════════════════════════════════════════════
  القواعد الذهبية الثلاث (AI Guardrails)
═══════════════════════════════════════════════════════════════════
1. كشف الشذوذ السعري: إذا كان سعر المنافس أقل من متوسط السوق بـ30%+
   → يُعلَّم كسعر شاذ (محروق) ولا يُطابَق.
2. حاجز الهبوط الأقصى: السعر المقترح لا ينزل أكثر من 15% عن سعرنا
   الحالي → يُوقف ويُرسل للمراجعة اليدوية.
3. إجماع السوق: 3+ منافسين بنفس السعر = سعر حقيقي جديد → يُثق به.
   منافس واحد بسعر منخفض جداً = شذوذ → يُرفض.
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from infrastructure.db_manager import DatabaseManager

# ════════════════════════════════════════════════════════════════════
#  مخطط الجدول (Schema)
# ════════════════════════════════════════════════════════════════════
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ai_decisions_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name     TEXT    NOT NULL,
    product_id       TEXT    DEFAULT '',
    competitor_name  TEXT    DEFAULT '',
    competitor_price REAL    DEFAULT 0.0,
    our_price        REAL    DEFAULT 0.0,
    market_average   REAL    DEFAULT 0.0,
    proposed_price   REAL    DEFAULT 0.0,
    ai_reason        TEXT    DEFAULT '',
    ai_confidence    REAL    DEFAULT 0.0,
    ai_decision      TEXT    DEFAULT '',
    ai_model         TEXT    DEFAULT '',
    safety_flag      TEXT    DEFAULT '',
    user_action      TEXT    DEFAULT 'pending',
    user_price       REAL    DEFAULT 0.0,
    user_note        TEXT    DEFAULT '',
    section          TEXT    DEFAULT '',
    batch_id         TEXT    DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at      TEXT    DEFAULT ''
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ai_dec_product  ON ai_decisions_log(product_name)",
    "CREATE INDEX IF NOT EXISTS idx_ai_dec_action   ON ai_decisions_log(user_action)",
    "CREATE INDEX IF NOT EXISTS idx_ai_dec_batch    ON ai_decisions_log(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_ai_dec_created  ON ai_decisions_log(created_at)",
]


# ════════════════════════════════════════════════════════════════════
#  نماذج البيانات (Data Models)
# ════════════════════════════════════════════════════════════════════
@dataclass
class AIDecision:
    """قرار تسعير واحد من AI مع سياق كامل."""

    product_name: str
    product_id: str = ""
    competitor_name: str = ""
    competitor_price: float = 0.0
    our_price: float = 0.0
    market_average: float = 0.0
    proposed_price: float = 0.0
    ai_reason: str = ""
    ai_confidence: float = 0.0
    ai_decision: str = ""        # raise / lower / hold / add
    ai_model: str = ""
    safety_flag: str = ""        # "" | "max_drop" | "outlier" | "consensus_weak"
    user_action: str = "pending"  # pending / approved / rejected / modified
    user_price: float = 0.0
    user_note: str = ""
    section: str = ""
    batch_id: str = ""
    id: Optional[int] = None
    created_at: str = ""
    resolved_at: str = ""


@dataclass
class SafetyResult:
    """نتيجة فحص الأمان لسعر مقترح."""

    safe_price: float             # السعر بعد التعديل (أو الأصلي إن آمن)
    flag: str = ""                # "" | "max_drop" | "outlier" | "consensus_weak"
    warning: str = ""             # رسالة تحذير للمستخدم
    is_safe: bool = True          # هل السعر آمن بلا تدخل؟
    original_proposed: float = 0.0  # السعر الأصلي قبل التعديل


@dataclass
class MarketContext:
    """سياق سوقي لمنتج واحد (يُحسب من بيانات المنافسين)."""

    prices: list[float] = field(default_factory=list)
    average: float = 0.0
    median: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    competitor_count: int = 0
    consensus_price: float = 0.0   # السعر الأكثر تكراراً
    consensus_count: int = 0       # عدد المنافسين عند سعر الإجماع
    has_consensus: bool = False    # 3+ منافسين بنفس السعر


# ════════════════════════════════════════════════════════════════════
#  القواعد الذهبية الثلاث (Guardrails)
# ════════════════════════════════════════════════════════════════════

def compute_market_context(competitor_prices: Sequence[float]) -> MarketContext:
    """يحسب السياق السوقي من قائمة أسعار المنافسين.

    يُستخدم لتغذية القاعدتين 1 (كشف الشذوذ) و3 (إجماع السوق).
    """
    valid = sorted(p for p in competitor_prices if p > 0)
    if not valid:
        return MarketContext()

    avg = sum(valid) / len(valid)
    mid = len(valid) // 2
    median = valid[mid] if len(valid) % 2 else (valid[mid - 1] + valid[mid]) / 2

    # إجماع السوق: نحسب تكرار كل سعر (مع هامش ±3% للتقريب)
    price_groups: dict[float, int] = {}
    tolerance = 0.03  # 3% هامش تقريب
    for price in valid:
        found = False
        for ref in price_groups:
            if abs(price - ref) / max(ref, 1) <= tolerance:
                price_groups[ref] += 1
                found = True
                break
        if not found:
            price_groups[price] = 1

    consensus_price = max(price_groups, key=price_groups.get) if price_groups else 0.0
    consensus_count = price_groups.get(consensus_price, 0)

    return MarketContext(
        prices=valid,
        average=round(avg, 2),
        median=round(median, 2),
        min_price=valid[0],
        max_price=valid[-1],
        competitor_count=len(valid),
        consensus_price=round(consensus_price, 2),
        consensus_count=consensus_count,
        has_consensus=consensus_count >= 3,
    )


def detect_outlier(
    competitor_price: float,
    market_average: float,
    *,
    threshold_pct: float = 0.30,
) -> bool:
    """القاعدة 1: هل سعر المنافس شاذ (أقل من المتوسط بـ30%+)؟"""
    if market_average <= 0 or competitor_price <= 0:
        return False
    drop = (market_average - competitor_price) / market_average
    return drop >= threshold_pct


def validate_price_safety(
    our_current_price: float,
    proposed_price: float,
    *,
    market_ctx: Optional[MarketContext] = None,
    max_drop_pct: float = 0.15,
    outlier_threshold: float = 0.30,
) -> SafetyResult:
    """يطبّق القواعد الذهبية الثلاث على السعر المقترح ويُعيد نتيجة الأمان.

    القاعدة 1 (الشذوذ): إذا كان السعر المقترح مبنياً على سعر شاذ →
        يرفع للمتوسط ويحذّر.
    القاعدة 2 (الهبوط الأقصى): إذا كان الهبوط > max_drop_pct →
        يوقف عند الحد الآمن ويرسل للمراجعة.
    القاعدة 3 (الإجماع): إذا وُجد إجماع سوقي (3+ منافسين) →
        يثق بالسعر حتى لو كان هبوطاً حاداً.
    """
    result = SafetyResult(
        safe_price=proposed_price,
        original_proposed=proposed_price,
    )

    # ── القاعدة 3 أولاً: الإجماع يتجاوز القواعد الأخرى ──
    if market_ctx and market_ctx.has_consensus:
        # 3+ منافسين بنفس السعر = سعر السوق الحقيقي → نثق به
        consensus = market_ctx.consensus_price
        # لكن نبقي حاجز الهبوط الأقصى كشبكة أمان أخيرة
        if our_current_price > 0:
            hard_floor = our_current_price * (1.0 - max_drop_pct)
            if consensus < hard_floor:
                result.safe_price = hard_floor
                result.flag = "max_drop"
                result.is_safe = False
                result.warning = (
                    f"إجماع سوقي عند {consensus:.0f} ر.س "
                    f"({market_ctx.consensus_count} منافسين) لكن الهبوط "
                    f"يتجاوز {max_drop_pct:.0%} — يرجى المراجعة اليدوية"
                )
                return result
        # الإجماع آمن → نقبله
        result.safe_price = consensus
        result.flag = ""
        result.is_safe = True
        return result

    # ── القاعدة 1: كشف الشذوذ ──
    if market_ctx and market_ctx.average > 0:
        if detect_outlier(proposed_price, market_ctx.average,
                          threshold_pct=outlier_threshold):
            # السعر المقترح مبني على سعر شاذ → نرفع للمتوسط
            result.safe_price = round(market_ctx.average, 2)
            result.flag = "outlier"
            result.is_safe = False
            result.warning = (
                f"السعر المقترح ({proposed_price:.0f}) شاذ — أقل من "
                f"متوسط السوق ({market_ctx.average:.0f}) بأكثر من "
                f"{outlier_threshold:.0%}. تم التعديل لقرب المتوسط."
            )
            proposed_price = result.safe_price  # لفحص القاعدة 2 أيضاً

    # ── القاعدة 2: حاجز الهبوط الأقصى ──
    if our_current_price > 0:
        safe_floor = our_current_price * (1.0 - max_drop_pct)
        if proposed_price < safe_floor:
            result.safe_price = round(safe_floor, 2)
            result.flag = result.flag or "max_drop"
            result.is_safe = False
            result.warning = (
                result.warning + " | " if result.warning else ""
            ) + (
                f"يتطلب هبوطاً بـ"
                f"{((our_current_price - proposed_price) / our_current_price):.0%} "
                f"(الحد الأقصى {max_drop_pct:.0%}) — يرجى المراجعة اليدوية"
            )
            return result

    return result


# ════════════════════════════════════════════════════════════════════
#  المستودع (Repository)
# ════════════════════════════════════════════════════════════════════

class AIDecisionRepository:
    """مستودع قرارات AI: CRUD + بحث ضبابي + أمثلة تعلّم (RAG)."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def ensure_table(self) -> None:
        """ينشئ الجدول والفهارس إن لم توجد."""
        self._db.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            self._db.execute(idx_sql)

    # ── إدراج ─────────────────────────────────────────────────────

    def log_decision(self, decision: AIDecision) -> int:
        """يدرج قرار AI واحداً ويُعيد الـ id."""
        cursor = self._db.execute(
            """INSERT INTO ai_decisions_log
               (product_name, product_id, competitor_name, competitor_price,
                our_price, market_average, proposed_price, ai_reason,
                ai_confidence, ai_decision, ai_model, safety_flag,
                user_action, section, batch_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision.product_name,
                decision.product_id,
                decision.competitor_name,
                decision.competitor_price,
                decision.our_price,
                decision.market_average,
                decision.proposed_price,
                decision.ai_reason,
                decision.ai_confidence,
                decision.ai_decision,
                decision.ai_model,
                decision.safety_flag,
                decision.user_action,
                decision.section,
                decision.batch_id,
            ),
        )
        return cursor.lastrowid or 0

    def log_batch(self, decisions: list[AIDecision]) -> list[int]:
        """يدرج دفعة كاملة ويُعيد قائمة الـ ids."""
        return [self.log_decision(d) for d in decisions]

    # ── تحديث (قرار المستخدم) ──────────────────────────────────────

    def resolve(
        self,
        decision_id: int,
        action: str,
        *,
        user_price: float = 0.0,
        note: str = "",
    ) -> None:
        """يسجّل قرار المستخدم (approved/rejected/modified) مع الوقت."""
        if action not in ("approved", "rejected", "modified"):
            raise ValueError(f"إجراء غير صالح: {action!r}")
        from core.product_entity import _utcnow
        now = _utcnow().isoformat(timespec="seconds")
        self._db.execute(
            """UPDATE ai_decisions_log
               SET user_action = ?, user_price = ?, user_note = ?,
                   resolved_at = ?
               WHERE id = ?""",
            (action, user_price, note, now, decision_id),
        )

    def log_redistribution_decision(
        self,
        product_name: str,
        old_section: str,
        new_section: str,
        ai_reason: str,
        ai_confidence: float,
        batch_id: str,
        product_id: str = "",
    ) -> int:
        """يسجّل قرار إعادة توزيع تلقائي.

        يستخدم ``log_decision`` مع ``user_action='auto_redistributed'``
        لتمييز القرارات الآلية عن اليدوية.
        """
        decision = AIDecision(
            product_name=product_name,
            product_id=product_id,
            ai_reason=ai_reason,
            ai_confidence=ai_confidence,
            ai_decision=new_section,
            section=old_section,
            user_action="auto_redistributed",
            batch_id=batch_id,
        )
        return self.log_decision(decision)

    # ── استعلامات قراءة ─────────────────────────────────────────

    def get_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """يجلب القرارات المعلّقة (pending) الأحدث أولاً."""
        rows = self._db.query(
            """SELECT * FROM ai_decisions_log
               WHERE user_action = 'pending'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def get_by_id(self, decision_id: int) -> Optional[dict[str, Any]]:
        """يجلب قرار AI واحداً بمعرّفه.

        Parameters
        ----------
        decision_id : int
            معرّف الصف في جدول ai_decisions_log.

        Returns
        -------
        Optional[dict[str, Any]]
            بيانات القرار كقاموس، أو None إذا لم يُعثر عليه.
        """
        rows = self._db.query(
            "SELECT * FROM ai_decisions_log WHERE id = ?",
            (decision_id,),
        )
        if rows:
            return dict(rows[0])
        return None

    def get_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        """يجلب كل قرارات دفعة معينة."""
        rows = self._db.query(
            "SELECT * FROM ai_decisions_log WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        )
        return [dict(r) for r in rows]

    def get_similar_decisions(
        self,
        product_name: str,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """يجلب آخر 5 قرارات مُحسَمة لمنتجات مشابهة (بحث LIKE للـ RAG).

        يبحث عن تطابق جزئي في اسم المنتج ويُعيد فقط القرارات المُحسَمة
        (approved/rejected/modified) ليتعلّم AI من تفضيلات المستخدم.
        """
        # نأخذ أول 3 كلمات من اسم المنتج كمفتاح بحث
        words = product_name.strip().split()[:3]
        if not words:
            return []
        pattern = f"%{'%'.join(words)}%"
        rows = self._db.query(
            """SELECT * FROM ai_decisions_log
               WHERE product_name LIKE ?
                 AND user_action IN ('approved', 'rejected', 'modified')
               ORDER BY resolved_at DESC LIMIT ?""",
            (pattern, limit),
        )
        return [dict(r) for r in rows]

    def get_learning_examples(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """يجلب أمثلة التعلّم (قرارات المستخدم المُحسَمة) لبناء Few-Shot.

        الأولوية: modified أولاً (تعلّم أقوى) ثم approved ثم rejected.
        """
        rows = self._db.query(
            """SELECT product_name, competitor_name, competitor_price,
                      our_price, proposed_price, user_action, user_price,
                      ai_decision, ai_reason, safety_flag
               FROM ai_decisions_log
               WHERE user_action IN ('approved', 'rejected', 'modified')
               ORDER BY
                   CASE user_action
                       WHEN 'modified' THEN 0
                       WHEN 'rejected' THEN 1
                       WHEN 'approved' THEN 2
                   END,
                   resolved_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, int]:
        """إحصاءات إجمالية: عدد كل حالة + إجمالي."""
        rows = self._db.query(
            """SELECT user_action, COUNT(*) as cnt
               FROM ai_decisions_log GROUP BY user_action"""
        )
        stats = {r["user_action"]: r["cnt"] for r in rows}
        stats["total"] = sum(stats.values())
        return stats

    def get_recent(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """آخر N قرار (للعرض في السجل)."""
        rows = self._db.query(
            "SELECT * FROM ai_decisions_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    # ── أدوات مساعدة ───────────────────────────────────────────

    @staticmethod
    def new_batch_id() -> str:
        """يولّد معرّف دفعة فريد."""
        from core.product_entity import _utcnow
        return f"batch_{_utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
