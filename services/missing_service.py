"""services/missing_service.py — كشف المنتجات المفقودة (نقل _compute_missing_from_store).

خط الأنابيب (#PRESERVED_LOGIC app.py:726-1050):
  1) مرشّحون من المخزن (يُحقَنون كـ ``candidates`` — مصدرهم CompetitorIntelligence).
  2) إزالة تكرار المتاجر بالاسم المجرّد (أرخص سعر).
  3) فلاتر الدقة: غير عطر/مجموعة/سعر متطرف/اسم قصير/بلا حجم/ميني<10مل.
  4) تحقّق ضبابي عبر ``MatchingService``: OWNED⇒إخفاء، REVIEW⇒محتمل، MISSING⇒green.
  5) تخزين قرصي بتوقيع ``F4v2|catalog_len|db_size`` (كتابة ذرّية).

خدمة نقية المنطق: المصدر والقاعدة محقونان، فتُختبر دون قاعدة بيانات حيّة.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

import pandas as pd

from conf.constants import (
    MISSING_CACHE_VERSION,
    MISSING_MAX_PRICE,
    MISSING_MIN_NAME_LEN,
    MISSING_MIN_PRICE,
    MISSING_MIN_SIZE_ML,
    PROJECT_ROOT,
)
from core.exceptions import MissingDetectionError
from services.matching_service import Ownership, MatchingService, miss_bare

# #PRESERVED_LOGIC: فئات وكلمات الإسقاط الحيّة (app.py:846-848).
_BAD_CLASSES = (
    "deodorant", "hair_mist", "body_mist", "body_lotion",
    "soap", "shower_gel", "after_shave", "rejected", "other",
)
_SET_WORDS = ("مجموعة", "مجموعه", "طقم", "gift set", "gift box", "set ")


@dataclass(frozen=True)
class ClassifyKernel:
    """دوال التصنيف من ``engines.engine`` (نقية، محقونة للاختبار)."""

    classify_product: Callable[[str], str]
    classify_category: Callable[[str], str]
    extract_size: Callable[[str], float]
    extract_brand: Callable[[str], str]
    is_sample: Callable[[str], bool]
    is_tester: Callable[[str], bool]


_CLASSIFY: Optional[ClassifyKernel] = None


def load_classify_kernel() -> ClassifyKernel:
    """يحمّل دوال التصنيف القانونية من ``engines.engine``."""
    global _CLASSIFY
    if _CLASSIFY is not None:
        return _CLASSIFY
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from engines.engine import (  # type: ignore
            classify_product,
            classify_product_category,
            extract_brand,
            extract_size,
            is_sample,
            is_tester,
        )
    except Exception as exc:  # pragma: no cover
        raise MissingDetectionError(
            "تعذّر تحميل دوال التصنيف من engines.engine", error=str(exc),
        ) from exc
    _CLASSIFY = ClassifyKernel(
        classify_product, classify_product_category, extract_size,
        extract_brand, is_sample, is_tester,
    )
    return _CLASSIFY


def is_non_perfume(name: str, price: float, kernel: ClassifyKernel) -> tuple[bool, str]:
    """يقرّر إسقاط المنتج + السبب. #PRESERVED_LOGIC app.py:854-878."""
    if kernel.classify_product(name) in _BAD_CLASSES:
        return True, "class"
    low = name.lower()
    if any(w in low for w in _SET_WORDS):
        return True, "set"
    if price > 0 and (price < MISSING_MIN_PRICE or price > MISSING_MAX_PRICE):
        return True, "price"
    if len(name.strip()) < MISSING_MIN_NAME_LEN:
        return True, "short"
    size = kernel.extract_size(name)
    if not size or size <= 0:
        return True, "nosize"
    if size < MISSING_MIN_SIZE_ML:
        return True, "mini"
    return False, ""


def item_type(name: str, kernel: ClassifyKernel) -> str:
    """يصنّف نوع السلعة. #PRESERVED_LOGIC app.py:880-886."""
    low = name.lower()
    if kernel.is_sample(name) or "ديكانت" in name or "تقسيم" in name:
        return "sample"
    if kernel.is_tester(name) or "تستر" in name or "tester" in low:
        return "tester"
    return "retail"


def missing_signature(catalog_len: int, db_size: "int | str") -> str:
    """توقيع الكاش ``<نسخة>|catalog_len|db_size|cap``. #PRESERVED_LOGIC app.py:770.

    ``cap`` = ``MISSING_MAX_UNIQUE`` أُضيف 2026-07-26: الحارس يحدّد **كم منتجاً
    يدخل الحساب أصلاً**، فتغييره يغيّر الناتج جذرياً (250,000⇒400,000 رفع
    المفقودات 36,393⇒61,977 وأعاد الشريحة فوق 402 ر.س كلها). وبدونه في التوقيع
    كان الكاش القديم يُعاد صامتاً فيُبطل التغيير بلا أثر مرئي — نفس الفخّ الذي
    استدعى رفع نسخة الكاش يدوياً هذه المرّة.
    """
    cap = str(os.environ.get("MISSING_MAX_UNIQUE", "250000")).strip() or "250000"
    return f"{MISSING_CACHE_VERSION}|{catalog_len}|{db_size}|{cap}"


def load_cache(path: str, signature: str) -> Optional[pd.DataFrame]:
    """يقرأ كاش المفقودات إن طابق التوقيع. #PRESERVED_LOGIC app.py:773-782."""
    if not signature or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cached = json.load(handle)
        if (isinstance(cached, dict) and cached.get("sig") == signature):
            df_data = cached.get("df")
            if isinstance(df_data, dict) and df_data.get("__type__") == "DataFrame":
                return pd.read_json(io.StringIO(df_data["data"]), orient="split")
    except Exception:
        return None
    return None


def save_cache(path: str, signature: str, df: pd.DataFrame) -> None:
    """كتابة ذرّية: ملف مؤقت ثم استبدال. #PRESERVED_LOGIC app.py:1040-1049."""
    if not signature:
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(
                {"sig": signature, "df": {"__type__": "DataFrame", "data": df.to_json(orient="split")}},
                handle, ensure_ascii=False,
            )
        os.replace(tmp, path)
    except Exception as exc:
        logging.getLogger("missing_service").warning(
            "تعذّر حفظ كاش المفقودات في %s — سيُعاد الحساب لاحقاً: %s", path, exc,
        )


class MissingService:
    """خدمة كشف المفقودات: تنقّي المرشّحين وتصنّفهم عبر المطابقة."""

    def __init__(
        self,
        matching: MatchingService,
        classify_kernel: Optional[ClassifyKernel] = None,
        our_brands: Optional[dict] = None,
    ) -> None:
        self._match = matching
        self._ck = classify_kernel or load_classify_kernel()
        # فهرس اسم منتجنا ← ماركته (اختياري): غيابه يعطّل R3 بأمان (R1/R2 فقط).
        self._our_brands = our_brands

    def _dedup(self, candidates: list[dict[str, Any]]) -> dict[str, tuple[dict, float]]:
        """دمج المرشّحين بالاسم المجرّد مع أرخص سعر. #PRESERVED_LOGIC app.py:833-842."""
        merged: dict[str, tuple[dict, float]] = {}
        for cand in candidates:
            bare = miss_bare(cand.get("product_name", ""), self._match.kernel)
            if not bare:
                continue
            price = float(cand.get("min_price", 0) or 0)
            existing = merged.get(bare)
            if existing is None or price < existing[1]:
                merged[bare] = (cand, price)
        return merged

    def _eligible(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """يُسقط غير الصالح **قبل** الدمج — كل مرشّح يُحاكَم باسمه وسعره هو.

        لماذا قبل الدمج (تشخيص 2026-07-26): ``miss_bare`` يُسقط الأرقام فتنهار كل
        أحجام المنتج في اسم مجرّد واحد، و``_dedup`` يُبقي **الأرخص** — وهو غالباً
        «عيّنة 1مل». ثم كان ``is_non_perfume`` يُسقط ذلك الفائز لأنه «ميني <10مل»
        ⇒ **تختفي العائلة كلها** ومعها الزجاجة الأصلية. قياس على القاعدة الحيّة:
        ``dior sauvage`` = 149 منتجاً (8–1,411 ر.س) يفوز بها سامبل 1مل بـ8 ر.س
        فتُسقَط جميعاً؛ وكذلك ``dior هوم`` 140 · ``versace eros`` 120 ·
        ``burberry هيرو`` 121 — وكلها غائبة عن المفقودات رغم أننا لا نملكها.

        التصفية أولاً تجعل الأرخص **الصالح** هو الفائز، فتظهر العائلة بسعرها
        الحقيقي. ``_dedup`` نفسه لم يُمَس (#PRESERVED_LOGIC) — تغيّر ما يُغذّى به فقط.
        """
        out: list[dict[str, Any]] = []
        for cand in candidates:
            name = str(cand.get("product_name", "") or "")
            price = float(cand.get("min_price", 0) or 0)
            dropped, _reason = is_non_perfume(name, price, self._ck)
            if not dropped:
                out.append(cand)
        return out

    def compute(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """يُنتج صفوف المفقودات. OWNED يُسقَط، REVIEW/MISSING يبقيان."""
        rows: list[dict[str, Any]] = []
        for cand, price in self._dedup(self._eligible(candidates)).values():
            name = str(cand.get("product_name", "") or "")
            outcome = self._match.evaluate(name, str(cand.get("brand", "") or ""))
            if outcome.ownership is Ownership.OWNED:
                continue  # نملكه باسم مختلف ⇒ ليس مفقوداً
            rows.append(self._build_row(name, price, cand, outcome))
        return rows

    def _build_row(
        self, name: str, price: float, cand: dict[str, Any], outcome: Any,
    ) -> dict[str, Any]:
        """يبني صف مفقود واحد بمخطّط app.py الحرفي. #PRESERVED_LOGIC app.py:992-1016."""
        comp_list = cand.get("competitors_list") or []
        # إصلاح عند المنبع: CompetitorIntelligence يسلّم القائمة نصاً مفصولاً
        # بفواصل ASCII — التكرار على النص كان يفتّت الأسماء حرفاً-حرفاً
        # («حنان» → «ح، ن، ا، ن»). القوائم الحقيقية تمرّ كما هي.
        if isinstance(comp_list, str):
            comp_list = [p.strip() for p in comp_list.split(",") if p.strip()]
        brand = str(cand.get("brand", "") or "").strip()
        if not brand or brand.lower() in ("nan", "none", "غير محدد"):
            brand = self._ck.extract_brand(name) or ""
        is_review = outcome.ownership is Ownership.REVIEW
        row = {
            "منتج_المنافس": name,
            "سعر_المنافس": price,
            "الماركة": brand,
            "المنافس": (comp_list[0] if comp_list else "")
            or f"{cand.get('competitor_count', 1)} متجر",
            "المنافسون": "، ".join(str(x).strip() for x in comp_list if str(x).strip()),
            "تصنيف_المنتج": str(cand.get("category", "") or "").strip()
            or self._ck.classify_category(name),
            "صورة_المنافس": str(cand.get("image_url", "") or ""),
            "image_urls": str(cand.get("image_urls", "") or ""),  # JSON: كل روابط الصور
            "السعر_المقترح": float(cand.get("suggested_price", 0) or 0),
            "مستوى_الثقة": "review" if is_review else "green",
            "درجة_التشابه": outcome.score,
            "منتج_مطابق_محتمل": outcome.our_match or "",
            "حالة_المراجعة": outcome.reason,
            "هو_تستر": item_type(name, self._ck) == "tester",
            "نوع_السلعة": item_type(name, self._ck),
            "عدد_المنافسين": int(cand.get("competitor_count", 1) or 1),
            # أقدم رصد للمنتج عبر المنافسين (لترتيب «الأحدث» وشارة 🆕). فارغ آمن.
            "تاريخ_أول_رصد": str(cand.get("first_seen_at", "") or ""),
        }
        # قواعد إعادة التصنيف (R1/R2/R3) — ترقية مقترحة فقط، لا حذف ولا مساس بـgreen.
        from services.reclassify_missing import propose
        level, reason, _prio = propose(row, self._our_brands)
        if level == "green" and row["مستوى_الثقة"] != "green":
            row["مستوى_الثقة"] = "green"
            row["حالة_المراجعة"] = ""
            row["سبب_النقل"] = reason
        return row

    @staticmethod
    def to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
        """يحوّل الصفوف إلى DataFrame (فارغ آمن)."""
        return pd.DataFrame(rows)
