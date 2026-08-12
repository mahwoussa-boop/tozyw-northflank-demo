"""
services/ai_prompts.py — قوالب التوجيه للموجّه الذكي (Zero-Loss AI Router)
═══════════════════════════════════════════════════════════════════════════
ملف بايثون صرف — بدون أي استيراد لـ Streamlit.

المسؤوليات:
  • ROUTING_SYSTEM_PROMPT  — بروفايل نظام لتحليل عناصر المراجعة
  • SALVAGE_SYSTEM_PROMPT  — بروفايل نظام لإعادة فحص المستبعدات
  • build_routing_prompt   — يبني سؤال المستخدم لدفعة منتجات مراجعة
  • build_salvage_prompt   — يبني سؤال المستخدم لدفعة منتجات مستبعدة
  • parse_ai_routing_response — يحلّل استجابة الذكاء الاصطناعي إلى قائمة قواميس
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  ثوابت داخلية
# ════════════════════════════════════════════════════════════════════════════
_MAX_BATCH: int = 8  # الحد الأقصى لحجم الدفعة الواحدة

# أنواع التصنيف المسموحة في الخرج
_VALID_MATCH_TYPES: frozenset[str] = frozenset({"MATCH", "MISSING", "UNSURE"})

# ════════════════════════════════════════════════════════════════════════════
#  بروفايل النظام — توجيه المنتجات (Routing)
# ════════════════════════════════════════════════════════════════════════════
ROUTING_SYSTEM_PROMPT: str = """\
أنت خبير مطابقة منتجات عطور (Perfume Product Matching Expert).

━━ مهمتك ━━
تتلقى قائمة منتجات مقترنة: (منتجنا ↔ منتج المنافس) مع نسبة تطابق آلي وسعرين.
عليك تحديد ما إذا كان الاقتران صحيحاً أم خاطئاً.

━━ قواعد التصنيف ━━
• MATCH  — المنتجان هما نفس العطر فعلاً (نفس الماركة، نفس الاسم الأساسي، \
نفس التركيز تقريباً EDP/EDT/Parfum، ونفس الحجم تقريباً ±10 مل).
• MISSING — المنتجان مختلفان (ماركة مختلفة، أو عطر مختلف تماماً، \
أو حجم مختلف جذرياً مثل 30ml مقابل 100ml). هذا يعني أن منتج المنافس \
ليس لدينا ما يقابله.
• UNSURE — لا تستطيع الجزم. ربما نفس الماركة لكن الاسم مبهم، \
أو التركيز مختلف (EDP مقابل EDT)، أو المعلومات ناقصة.

⚠️ قاعدة ذهبية: إذا لم تكن متأكداً ١٠٠٪، اختر UNSURE. \
الخطأ في التصنيف أسوأ بكثير من عدم اليقين.

━━ معرفة تخصصية بأسماء العطور ━━
• التركيز: EDP (Eau de Parfum) ≠ EDT (Eau de Toilette) ≠ Parfum/Extrait — \
إذا اختلف التركيز فالمنتج مختلف عملياً → MISSING أو UNSURE.
• الحجم: يُذكر عادةً بـ ml أو مل. فرق ±10 مل مقبول (مثل 98ml≈100ml).
• الماركة: قد تُكتب بالعربي أو الإنجليزي (ديور=Dior، شانيل=Chanel).
• أسماء مشتركة: بعض العطور لها أسماء متقاربة جداً ضمن نفس الماركة \
(مثل "Intense" و"Extreme" و"Absolute") — تعامل معها كمنتجات مختلفة → MISSING.

━━ شكل الخرج ━━
أرجع مصفوفة JSON فقط — بدون أي نص خارجها. كل عنصر:
{{"index": <int>, "match_type": "MATCH|MISSING|UNSURE", "confidence": <0.0-1.0>, "reason": "<سبب مختصر>"}}
"""

# ════════════════════════════════════════════════════════════════════════════
#  بروفايل النظام — إنقاذ المستبعدات (Salvage)
# ════════════════════════════════════════════════════════════════════════════
SALVAGE_SYSTEM_PROMPT: str = """\
أنت خبير مطابقة منتجات عطور. مهمتك إعادة فحص منتجات مستبعدة (Excluded).

━━ السياق ━━
هذه المنتجات استُبعدت سابقاً لأن النظام الآلي لم يجد لها تطابقاً جيداً \
في كتالوجنا. لكن بعضها قد يكون منتجاً حقيقياً نبيعه فعلاً تحت اسم مختلف.

━━ مهمتك ━━
لكل منتج مستبعد، حدد:
• MATCH  — نحن نبيع هذا المنتج فعلاً (وجدت تطابقاً مع اسمنا المرفق).
• MISSING — هذا منتج حقيقي لا نبيعه ويجب إضافته لقائمة المفقودات.
• UNSURE — لا تستطيع الجزم (معلومات ناقصة، اسم غامض، أو منتج غير عطري).

━━ تنبيهات ━━
• بعض المنتجات المستبعدة ليست عطوراً أصلاً (عيّنات، بخاخات جسم، مزيلات عرق) — \
صنّفها UNSURE.
• إذا كان اسمنا فارغاً ("") فلا يمكن أن يكون MATCH → إما MISSING أو UNSURE.
• إذا شككت: UNSURE أفضل من خطأ.

━━ شكل الخرج ━━
مصفوفة JSON فقط — بدون أي نص خارجها. كل عنصر:
{{"index": <int>, "match_type": "MATCH|MISSING|UNSURE", "confidence": <0.0-1.0>, "reason": "<سبب مختصر>"}}
"""


# ════════════════════════════════════════════════════════════════════════════
#  بناة الأسئلة (Prompt Builders)
# ════════════════════════════════════════════════════════════════════════════

def build_routing_prompt(rows: list[dict]) -> str:
    """يبني سؤال المستخدم لدفعة منتجات مراجعة (حد أقصى 8).

    Parameters
    ----------
    rows : list[dict]
        كل قاموس يحوي المفاتيح:
        ``our_name``, ``comp_name``, ``our_price``, ``comp_price``,
        ``match_score``, ``brand``, ``decision``

    Returns
    -------
    str
        نص السؤال الجاهز للإرسال إلى الذكاء الاصطناعي.
    """
    if not rows:
        return ""

    # قطع الدفعة عند الحد الأقصى
    batch = rows[:_MAX_BATCH]

    lines: list[str] = [
        f"حلّل المنتجات التالية ({len(batch)} عنصر) "
        f"وأرجع مصفوفة JSON بنفس الترتيب:\n"
    ]

    for idx, row in enumerate(batch):
        our_name   = row.get("our_name", "—")
        comp_name  = row.get("comp_name", "—")
        our_price  = row.get("our_price", "?")
        comp_price = row.get("comp_price", "?")
        score      = row.get("match_score", "?")
        brand      = row.get("brand", "—")
        decision   = row.get("decision", "—")

        lines.append(
            f"[{idx}] منتجنا: «{our_name}» (سعرنا: {our_price} ر.س) | "
            f"المنافس: «{comp_name}» (سعره: {comp_price} ر.س) | "
            f"تطابق آلي: {score}% | الماركة: {brand} | القرار الآلي: {decision}"
        )

    lines.append(
        "\nأرجع مصفوفة JSON فقط بطول "
        f"{len(batch)} عنصر. لا تضف أي نص شرحي خارج الـ JSON."
    )

    return "\n".join(lines)


def build_salvage_prompt(rows: list[dict]) -> str:
    """يبني سؤال المستخدم لدفعة منتجات مستبعدة (حد أقصى 8).

    Parameters
    ----------
    rows : list[dict]
        كل قاموس يحوي المفاتيح:
        ``our_name`` (قد يكون فارغاً), ``comp_name``, ``comp_price``,
        ``brand``, ``decision``

    Returns
    -------
    str
        نص السؤال الجاهز للإرسال إلى الذكاء الاصطناعي.
    """
    if not rows:
        return ""

    batch = rows[:_MAX_BATCH]

    lines: list[str] = [
        f"أعد فحص المنتجات المستبعدة التالية ({len(batch)} عنصر) "
        f"وأرجع مصفوفة JSON بنفس الترتيب:\n"
    ]

    for idx, row in enumerate(batch):
        our_name   = row.get("our_name", "") or ""
        comp_name  = row.get("comp_name", "—")
        comp_price = row.get("comp_price", "?")
        brand      = row.get("brand", "—")
        decision   = row.get("decision", "—")

        our_part = f"أقرب منتج لدينا: «{our_name}»" if our_name else "لا يوجد تطابق لدينا"
        lines.append(
            f"[{idx}] المنافس: «{comp_name}» (سعره: {comp_price} ر.س) | "
            f"الماركة: {brand} | القرار السابق: {decision} | {our_part}"
        )

    lines.append(
        "\nأرجع مصفوفة JSON فقط بطول "
        f"{len(batch)} عنصر. لا تضف أي نص شرحي خارج الـ JSON."
    )

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
#  محلّل الاستجابة (Response Parser)
# ════════════════════════════════════════════════════════════════════════════

def parse_ai_routing_response(text: str) -> list[dict] | None:
    """يحلّل استجابة الذكاء الاصطناعي إلى قائمة قواميس موحّدة.

    يتعامل مع JSON داخل أسوار ```json``` ومع JSON مجرّد.
    يُعقّم كل عنصر: يتأكد من وجود المفاتيح المطلوبة ويُطبّق القيم الافتراضية.

    Parameters
    ----------
    text : str
        النص الخام من استجابة الذكاء الاصطناعي.

    Returns
    -------
    list[dict] | None
        قائمة قواميس بالمفاتيح ``index``, ``match_type``, ``confidence``,
        ``reason``، أو ``None`` إذا فشل التحليل.
    """
    if not text or not text.strip():
        log.warning("parse_ai_routing_response: استجابة فارغة")
        return None

    # ── الاستعانة بـ parse_json المشتركة (تتعامل مع أسوار JSON) ──
    from services.ai_service import parse_json  # استيراد كسول — بدون دورة

    parsed: Any = parse_json(text)

    if parsed is None:
        log.warning("parse_ai_routing_response: فشل parse_json في استخراج JSON")
        return None

    # ── التأكد من أن الخرج مصفوفة ──
    if isinstance(parsed, dict):
        # بعض النماذج تُغلّف المصفوفة في مفتاح واحد
        # مثال: {"results": [...]}
        for key in ("results", "items", "data", "products"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break
        else:
            # قاموس وحيد → نضعه في مصفوفة
            parsed = [parsed]

    if not isinstance(parsed, list):
        log.warning("parse_ai_routing_response: الخرج ليس مصفوفة — %s", type(parsed))
        return None

    # ── تعقيم كل عنصر ──
    sanitized: list[dict] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            log.debug("parse_ai_routing_response: العنصر %d ليس قاموساً — تجاوز", i)
            continue

        match_type = str(item.get("match_type", "UNSURE")).upper().strip()
        if match_type not in _VALID_MATCH_TYPES:
            log.debug(
                "parse_ai_routing_response: نوع غير معروف '%s' في العنصر %d → UNSURE",
                match_type, i,
            )
            match_type = "UNSURE"

        # ثقة بين 0 و 1
        try:
            confidence = float(item.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        sanitized.append({
            "index": item.get("index", i),
            "match_type": match_type,
            "confidence": round(confidence, 3),
            "reason": str(item.get("reason", "")),
        })

    if not sanitized:
        log.warning("parse_ai_routing_response: لم يبقَ أي عنصر صالح بعد التعقيم")
        return None

    return sanitized
