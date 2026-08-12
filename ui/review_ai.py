"""ui/review_ai.py — إعادة توزيع «تحت المراجعة» بالذكاء الاصطناعي (المرحلة B).

كل صفّ مراجعة لديه منتج مرشّح (``منتج_مطابق_محتمل``) لكنّ المحرّك أبقاه للمراجعة
لتعثّر حارس (حجم/ماركة/هيكل). نسأل AI سؤالاً واحداً لكل زوج (منافس↔مرشّحنا):
أهُما **نفس العطر** (مكرر) أم عطر مختلف (فريد) أم غير مؤكد؟ ثم نُعيد التوزيع:
- ``مكرر`` ⇒ يُزال من المفقودات ويُلحَق ببطاقة منتجنا (يعيد استخدام ``ui.reconcile``).
- ``فريد`` ⇒ يبقى مفقوداً (يُرفَّع لاحقاً كمؤكد).
- ``غير مؤكد`` ⇒ يبقى تحت المراجعة (لا حذف، لا تخمين).

التكلفة منخفضة: دفعات 8 أزواج لكل نداء + كاش ``AIService`` (نداءات متطابقة مجانية).
كل الدوال الخالصة (بناء البرومت/تحليل الرد/التصنيف بخدمة محقونة) قابلة للاختبار
بلا أي نداء AI حقيقي (rule#8: الاختبارات تحقن خدمة وهمية فقط).

#PRESERVED_LOGIC: نمط حكم AI مطابق لتصنيف أداة المستخدم ([AI] نفس الماركة/الاسم/الحجم).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from conf.constants import COL_BRAND, COL_COMP_NAME, COL_COMP_PRICE
from ui.reconcile import _COL_MATCHED, _s, apply_reconciliation

DUPLICATE = "duplicate"
UNIQUE = "unique"
UNSURE = "unsure"
_VERDICT_AR = {DUPLICATE: "مكرر", UNIQUE: "فريد", UNSURE: "غير مؤكد"}
_DECISIONS_FILE = "review_ai_decisions.jsonl"  # سجلّ القرارات + تصحيحات المستخدم

_SYSTEM = (
    "أنت خبير عطور دقيق. لكل زوج، حدّد إن كان منتج المنافس هو نفس عطر منتجنا "
    "المرشّح. ⚠️ مهم: اختلاف الإملاء/الترجمة/الحروف اللاتينية مقابل العربية/"
    "التشكيل/ترتيب الكلمات/التركيز (او دو تواليت ↔ او دو بارفيوم) لا يعني "
    "اختلاف المنتج — احكم بالماركة والاسم الأساسي والحجم فقط. اعتبرهما «مكرر» "
    "إذا اتفقت الماركة والاسم الأساسي والحجم (ولو بصياغة مختلفة). اعتبرهما «فريد» "
    "إذا اختلفت الماركة أو الاسم الأساسي أو الحجم جوهرياً. «غير مؤكد» إن غمض الأمر. "
    "لكل زوج أعطِ أيضاً هل الماركة متطابقة (brand_match) وهل الحجم متطابق (size_match)."
)

# أمثلة few-shot افتراضية مستخلصة من حالات حقيقية تعلَّمناها (تُحقَن/تُحمَّل من
# ملفّات المستخدم المُتحقَّقة عند التشغيل — تعلُّم سياقي مستمر). #PRESERVED_LOGIC
_DEFAULT_FEWSHOT: tuple[dict[str, str], ...] = (
    {"comp": "عطر ريجينس -100مل", "cand": "عطر ريجنس او دو بارفيوم تواليت 100مل-نسائي",
     "verdict": "مكرر"},   # «ريجينس/ريجنس» اختلاف إملاء فقط
    {"comp": "نارسيسو رودريغيز اول اوف مي او دي بارفيوم 90 مل",
     "cand": "عطر نرسيسو رودريغز أول أوف مي أو دو برفيوم 90 مل", "verdict": "مكرر"},
    {"comp": "عطر بوهوبوكو سي سلت كاراميل 50 مل",
     "cand": "عطر بوهوبوكو سي سولت كراميل أو دو بارفيوم 50 مل", "verdict": "مكرر"},
    {"comp": "تستر الجنسو برافو مونسيور او دو تواليت 125مل",
     "cand": "عطر ثامين برافو إكستريت دو بارفيوم 100مل", "verdict": "فريد"},  # ماركة+حجم مختلفان
)


def build_pair(row: Any) -> dict[str, str]:
    """يستخرج (المنافس، المرشّح، الماركة، السعر) من صفّ مراجعة. آمن من None/NaN."""
    data = row if isinstance(row, dict) else dict(row)
    return {
        "comp": _s(data.get(COL_COMP_NAME)),
        "cand": _s(data.get(_COL_MATCHED)),
        "brand": _s(data.get(COL_BRAND)),
        "price": _s(data.get(COL_COMP_PRICE)),
    }


def build_batch_prompt(
    pairs: list[dict[str, str]],
    examples: tuple[dict[str, str], ...] = _DEFAULT_FEWSHOT,
) -> str:
    """يبني برومت دفعة (≤8 أزواج) مع أمثلة few-shot، يطلب JSON array منظَّماً."""
    lines = ["صنّف كل زوج: هل منتج المنافس ومنتجنا المرشّح نفس العطر؟", ""]
    if examples:
        lines.append("أمثلة محسومة (تعلّم منها معيار الحكم):")
        for ex in examples:
            lines.append(f"  المنافس «{ex['comp']}» | منتجنا «{ex['cand']}» ⇒ {ex['verdict']}")
        lines.append("")
    lines.append("الأزواج المطلوب حكمها:")
    for i, pair in enumerate(pairs):
        brand = f" (ماركة: {pair['brand']})" if pair["brand"] else ""
        lines.append(
            f"[{i}] المنافس: «{pair['comp']}»{brand} | منتجنا: «{pair['cand']}»"
        )
    lines += [
        "",
        "أعِد فقط JSON array بلا أي نص آخر بهذا الشكل:",
        '[{"i":0,"verdict":"مكرر","brand_match":true,"size_match":true}]',
        "حيث verdict إحدى: «مكرر» أو «فريد» أو «غير مؤكد»؛ وbrand_match/size_match قيمتان منطقيتان.",
    ]
    return "\n".join(lines)


def normalize_verdict(text: Any) -> str:
    """يحوّل نص الحكم إلى ثابت (duplicate/unique/unsure). افتراضه unsure (آمن)."""
    token = _s(text).lower()
    if "مكرر" in token or "duplicate" in token:
        return DUPLICATE
    if "فريد" in token or "unique" in token:
        return UNIQUE
    return UNSURE


def _as_bool(value: Any) -> bool:
    """منطقي آمن من true/false/«نعم»/«لا»/None."""
    if isinstance(value, bool):
        return value
    token = _s(value).lower()
    return token in ("true", "1", "yes", "نعم", "صحيح", "متطابق")


def parse_batch_response(response: Any, count: int) -> list[dict[str, Any]]:
    """يحلّل رد AI إلى ``count`` حكماً منظَّماً {verdict, brand_match, size_match}.

    آمن: أي تعذّر ⇒ unsure للجميع. ``brand_match``/``size_match`` غائبان ⇒ True
    (لا نعاقب نموذجاً لم يُعِد الحقلين؛ الحارس يبقى فعّالاً عند إعادتهما False).
    """
    verdicts: list[dict[str, Any]] = [
        {"verdict": UNSURE, "brand_match": False, "size_match": False}
        for _ in range(count)
    ]
    text = _s(response)
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return verdicts
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, TypeError):
        return verdicts
    if not isinstance(parsed, list):
        return verdicts
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("i"))
        except (ValueError, TypeError):
            continue
        if 0 <= idx < count:
            verdicts[idx] = {
                "verdict": normalize_verdict(item.get("verdict")),
                "brand_match": _as_bool(item.get("brand_match", True)),
                "size_match": _as_bool(item.get("size_match", True)),
            }
    return verdicts


def finalize_verdict(item: dict[str, Any], *, guard: bool = True) -> str:
    """يطبّق حارس الأمان: «مكرر» يُقبَل فقط بتوافق الماركة والحجم (دلالياً).

    دون ذلك يُخفَّض إلى «غير مؤكد» (يبقى تحت المراجعة، لا يُحذف منتج حقيقي).
    """
    verdict = item.get("verdict", UNSURE)
    if guard and verdict == DUPLICATE and not (
        item.get("brand_match") and item.get("size_match")
    ):
        return UNSURE
    return verdict


def classify_rows(
    rows: list[Any],
    ai_service: Any,
    *,
    examples: tuple[dict[str, str], ...] = _DEFAULT_FEWSHOT,
    guard: bool = True,
    chunk_size: int = 8,
    progress_cb: Optional[Callable[[int, int], Any]] = None,
) -> list[str]:
    """يصنّف صفوف المراجعة دفعات عبر ``ai_service.call`` (محقون ⇒ لا شبكة في الاختبار).

    يُعيد حكماً نهائياً لكل صفّ (بعد الحارس). فشل دفعة ⇒ unsure (لا انهيار).
    """
    pairs = [build_pair(r) for r in rows]
    verdicts: list[str] = []
    total = len(pairs)
    for start in range(0, total, chunk_size):
        chunk = pairs[start:start + chunk_size]
        try:
            result = ai_service.call(build_batch_prompt(chunk, examples), _SYSTEM)
            response = result.response if getattr(result, "success", False) else ""
        except Exception:  # noqa: BLE001 — صفّ المراجعة لا يُحذف عند فشل AI
            response = ""
        structured = parse_batch_response(response, len(chunk))
        verdicts.extend(finalize_verdict(s, guard=guard) for s in structured)
        if progress_cb:
            progress_cb(len(verdicts), total)
    return verdicts


def confirmed_duplicates_df(rows: list[Any], verdicts: list[str]) -> pd.DataFrame:
    """يبني DataFrame من صفوف المراجعة التي حكم AI بأنها «مكرر» (مخطط متوافق مع reconcile)."""
    dups = [
        (row if isinstance(row, dict) else dict(row))
        for row, verdict in zip(rows, verdicts)
        if verdict == DUPLICATE
    ]
    return pd.DataFrame(dups) if dups else pd.DataFrame()


def verdict_to_arabic(verdict: Any) -> str:
    """يحوّل ثابت الحكم إلى نصّه العربي (للعرض/الأمثلة)."""
    return _VERDICT_AR.get(_s(verdict), "غير مؤكد")


def build_decision_records(
    rows: list[Any], verdicts: list[str], *, source: str = "ai",
) -> list[dict[str, str]]:
    """يبني سجلّات قرار {comp, cand, verdict, source} للتدوين. خالص."""
    records: list[dict[str, str]] = []
    for row, verdict in zip(rows, verdicts):
        pair = build_pair(row)
        records.append({
            "comp": pair["comp"], "cand": pair["cand"],
            "verdict": _s(verdict), "source": source,
        })
    return records


def corrections_to_fewshot(
    records: list[Any], *, limit: int = 6,
) -> tuple[dict[str, str], ...]:
    """يحوّل تصحيحات المستخدم (source=user) إلى أمثلة few-shot (تعلّم مستمر). خالص."""
    out: list[dict[str, str]] = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("source") != "user":
            continue
        comp, cand = _s(rec.get("comp")), _s(rec.get("cand"))
        if comp and cand:
            out.append({"comp": comp, "cand": cand,
                        "verdict": verdict_to_arabic(rec.get("verdict"))})
    return tuple(out[-limit:])


def append_jsonl(path: Any, records: list[dict[str, Any]]) -> None:
    """يلحق سجلّات بملف JSONL (UTF-8). يُنشئ المجلد إن لزم."""
    if not records:
        return
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(path: Any) -> list[dict[str, Any]]:
    """يقرأ ملف JSONL إلى قائمة قواميس (آمن: ملف غائب/سطر تالف ⇒ يتخطّى)."""
    file = Path(path)
    if not file.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def learned_examples(data_dir: Any, *, limit: int = 6) -> tuple[dict[str, str], ...]:
    """أمثلة few-shot = الافتراضية + تصحيحات المستخدم المُدوَّنة (تعلّم مستمر)."""
    records = read_jsonl(Path(data_dir) / "reconcile" / _DECISIONS_FILE)
    learned = corrections_to_fewshot(records, limit=limit)
    return _DEFAULT_FEWSHOT + learned


def redistribute_reviews(
    missing_df: Any,
    sections: Any,
    rows: list[Any],
    verdicts: list[str],
) -> tuple[Any, Any, dict[str, int]]:
    """يُعيد توزيع المكرر المؤكَّد بالـAI (إزالة + إلحاق) عبر ``ui.reconcile``. خالص.

    ``فريد``/``غير مؤكد`` تبقى في المفقودات/المراجعة دون مساس. يُعيد
    (missing, sections, stats) مع عدّ كل حكم.
    """
    dup_df = confirmed_duplicates_df(rows, verdicts)
    new_missing, new_sections, stats = apply_reconciliation(
        missing_df, sections, dup_df=dup_df,
    )
    stats = dict(stats)
    stats["verdict_duplicate"] = sum(1 for v in verdicts if v == DUPLICATE)
    stats["verdict_unique"] = sum(1 for v in verdicts if v == UNIQUE)
    stats["verdict_unsure"] = sum(1 for v in verdicts if v == UNSURE)
    return new_missing, new_sections, stats
