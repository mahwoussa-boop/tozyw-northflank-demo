"""
engines/closed_loop_engine.py  v1.0 — محرك المطابقة الدائري المغلق
═══════════════════════════════════════════════════════════════════════

قانون حفظ البيانات (الدائرة المغلقة):
    المدخل = المتطابق + المراجعة_اليدوية + المفقود
    أي انتهاك لهذه المعادلة → RuntimeError فوري.

الشلال الرباعي (Waterfall):
    الطبقة 1 — SKU/Barcode (تطابق قطعي 100%)
    الطبقة 2 — نص تام بعد التطبيع (تطابق 100%)
    الطبقة 3 — RapidFuzz + تحقق هيكلي (حجم + ماركة + نوع)
    الطبقة 4 — شبكة الأمان (لا يسقط حرف واحد)

منطق التصنيف:
    score >= 90%  AND  هيكل مطابق  →  متطابق  (MATCHED)
    score >= 90%  BUT  هيكل مختلف  →  مراجعة (REVIEW)
    65% <= score < 90%              →  مراجعة (REVIEW)
    score < 65%                     →  مفقود  (MISSING)
    لا مرشح نهائياً                →  مراجعة (REVIEW) — طبقة 4

بصمة القرار (Audit Trail):
    كل صف يحمل: Match_Score + Match_Reason + طبقة_المطابقة

التطبيع غير المدمر (Non-Destructive):
    Raw_Name       — الاسم الخام لا يُمس أبداً
    Normalized_Name — نسخة مطبّعة للمطابقة فقط

⚠️  للتحليل والعرض فقط — لا يُعدِّل أي أسعار ولا يتصل بأي API ⚠️
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz import process as rf_process

# ─── إعداد المُسجِّل ─────────────────────────────────────────────────────────
logger = logging.getLogger("closed_loop_engine")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] closed_loop: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


# ══════════════════════════════════════════════════════════════════════════════
#  الثوابت
# ══════════════════════════════════════════════════════════════════════════════

CONFIRMED_SCORE: float = 90.0
"""نسبة التطابق الدنيا للمطابقة المؤكدة (مع اجتياز التحقق الهيكلي)."""

REVIEW_LOWER: float = 65.0
"""حد المنطقة الرمادية — ما بين 65% و 89% → مراجعة يدوية."""

# حالات التصنيف — مستخدمة كمفاتيح ثابتة في كل مكان
STATUS_MATCHED  = "متطابق"
STATUS_REVIEW   = "مراجعة_يدوية"
STATUS_MISSING  = "مفقود"
STATUS_EXCLUDED = "مستبعد"          # تعارض هيكلي مؤكد — يُفصَل عن «مفقود» (v2.0)

# أعمدة كتالوج سلة الشامل
CAT_PK    = "No."
CAT_NAME  = "أسم المنتج"
CAT_PRICE = "سعر المنتج"


# ══════════════════════════════════════════════════════════════════════════════
#  خريطة التطبيع العربي (Non-Destructive — تُطبَّق على نسخة مؤقتة فقط)
# ══════════════════════════════════════════════════════════════════════════════

_AR_CHAR_MAP: Dict[str, str] = {
    # توحيد الألف
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    # توحيد التاء والياء
    "ة": "ه",
    "ى": "ي",
    # إزالة التشكيل (حركات + تنوين + شدة + سكون)
    "\u064B": "", "\u064C": "", "\u064D": "",
    "\u064E": "", "\u064F": "", "\u0650": "",
    "\u0651": "", "\u0652": "",
    # إزالة المدة والهمزة المفردة
    "\u0653": "", "\u0654": "", "\u0655": "",
}
_AR_TRANS_TABLE = str.maketrans(_AR_CHAR_MAP)

# تصحيحات الماركات (مع حدود الكلمات \b) — أحرف عربية → إنجليزية موحّدة
# مرتبة من الأطول للأقصر لتجنب التعارض
_BRAND_CORRECTIONS: List[Tuple[str, str]] = [
    ("ايف سان لوران", "ysl"),
    ("ايف سان لورن",  "ysl"),
    ("جان بول غوتييه", "jean paul gaultier"),
    ("كارولينا هيريرا", "carolina herrera"),
    ("نارسيسو رودريجيز", "narciso rodriguez"),
    ("دولتشي وغابانا", "dolce gabbana"),
    ("توم فورد",    "tom ford"),
    ("أرماني",      "armani"),
    ("ارماني",      "armani"),
    ("غيرلان",      "guerlain"),
    ("ديور",        "dior"),
    ("شانيل",       "chanel"),
    ("غوتشي",       "gucci"),
    ("برادا",       "prada"),
    ("برادة",       "prada"),
    ("بربري",       "burberry"),
    ("هيرمس",       "hermes"),
    ("كارتييه",     "cartier"),
    ("فالنتينو",    "valentino"),
    ("فيرساتشي",    "versace"),
    ("غيفنشي",      "givenchy"),
    ("لانكوم",      "lancome"),
    ("إيزي مياكي",  "issey miyake"),
    ("ايزي مياكي",  "issey miyake"),
    ("داوود هوف",   "davidoff"),
    ("كالفن كلين",  "calvin klein"),
    ("سوفاج",       "sauvage"),
    ("لطافة",       "lattafa"),
    ("رصاصي",       "rasasi"),
    ("أجمل",        "ajmal"),
    ("ناهد",        "nabeel"),
    ("كريد",        "creed"),
    ("أمواج",       "amouage"),
]

# تطبيع مصطلحات التركيز/النوع (تُطبَّق بعد تحويل الكل إلى lowercase)
_TYPE_WORD_MAP: List[Tuple[str, str]] = [
    ("او دو بارفان",  "edp"),
    ("أو دو بارفان",  "edp"),
    ("او دي بارفان",  "edp"),
    ("بارفيم",        "edp"),
    ("بارفام",        "edp"),
    ("بارفان",        "edp"),
    ("eau de parfum", "edp"),
    ("او دو تواليت",  "edt"),
    ("أو دو تواليت",  "edt"),
    ("او دي تواليت",  "edt"),
    ("تواليت",        "edt"),
    ("eau de toilette","edt"),
    ("او دي كولونيا", "edc"),
    ("كولونيا",       "edc"),
    ("eau de cologne","edc"),
    ("ملي",           "ml"),
    ("مل",            "ml"),
]

# استخراج الحجم: أرقام متبوعة بـ ml/مل/oz
_VOLUME_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:ml|مل|oz|fl\.?\s*oz)",
    re.IGNORECASE | re.UNICODE,
)

# استخراج نوع التركيز
_TYPE_RE = re.compile(
    r"\b(edp|edt|edc|parfum|extrait|extract"
    r"|eau\s+de\s+parfum|eau\s+de\s+toilette|eau\s+de\s+cologne"
    r"|بارفيم|بارفام|بارفان|تواليت|كولونيا)\b",
    re.IGNORECASE | re.UNICODE,
)


# ══════════════════════════════════════════════════════════════════════════════
#  1.  التطبيع غير المدمر — normalize_text
# ══════════════════════════════════════════════════════════════════════════════

def normalize_text(raw: str) -> str:
    """
    يُنتج نسخة مطبَّعة من الاسم للمطابقة دون تعديل الاسم الأصلي.

    الخطوات بالترتيب:
    1. توحيد الأحرف العربية (أ/إ/آ → ا ، ة → ه ، ى → ي).
    2. إزالة التشكيل والحركات.
    3. تصحيح أسماء الماركات بحدود كلمات Unicode (``(?<!\\w)...(?!\\w)``).
    4. تطبيع مصطلحات التركيز (EDP/EDT ...).
    5. تحويل لأحرف صغيرة + ضغط المسافات.

    Parameters
    ----------
    raw : str
        الاسم الخام. لا يُعدَّل أبداً.

    Returns
    -------
    str
        نسخة جديدة مطبَّعة. الـ ``raw`` لا يتغير.
    """
    text: str = str(raw)

    # الخطوة 1+2: توحيد الأحرف العربية + إزالة التشكيل
    text = text.translate(_AR_TRANS_TABLE)

    # الخطوة 3: تصحيح الماركات بـ word boundaries (Unicode — آمن مع العربية)
    for wrong, correct in _BRAND_CORRECTIONS:
        text = re.sub(
            r"(?<!\w)" + re.escape(wrong) + r"(?!\w)",
            correct,
            text,
            flags=re.IGNORECASE | re.UNICODE,
        )

    # تحويل لأحرف صغيرة قبل تطبيع المصطلحات
    text = text.lower()

    # الخطوة 4: توحيد مصطلحات النوع
    for wrong, correct in _TYPE_WORD_MAP:
        text = re.sub(
            r"(?<!\w)" + re.escape(wrong.lower()) + r"(?!\w)",
            correct,
            text,
            flags=re.UNICODE,
        )

    # الخطوة 5: إزالة الرموز الخاصة غير الأبجدية الرقمية + ضغط المسافات
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ══════════════════════════════════════════════════════════════════════════════
#  1-bis.  التنظيف العميق — deep_clean  (الدائرة المقفلة v2.0)
#  تمريرة واحدة O(طول الاسم) · lru_cache · إزالة ضجيج محمية (لا تُفرّغ الاسم)
# ══════════════════════════════════════════════════════════════════════════════

# توحيد الأرقام العربية/الفارسية → ASCII (جدول مستقل — لا يمسّ normalize_text).
_DIGIT_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _fold(s: Any) -> str:
    """طيّ الأحرف العربية + التشكيل + الأرقام + خفض الحالة — لمواءمة المفاتيح والنص."""
    return str(s).translate(_AR_TRANS_TABLE).translate(_DIGIT_TRANS).lower()


# خريطة الاستبدال (ماركات + أنواع): المفاتيح مطويّة لتطابق النص المطويّ —
# إصلاح علّة كامنة في normalize_text حيث لم يكن "أمواج" يُطابَق بعد الطيّ إلى
# "امواج". الترتيب أطول-أولاً في البديل لمحاكاة المطابقة الأطول.
_REPL_MAP: Dict[str, str] = {}
for _wrong, _correct in (_BRAND_CORRECTIONS + _TYPE_WORD_MAP):
    _fk = _fold(_wrong)
    if _fk and _fk not in _REPL_MAP:
        _REPL_MAP[_fk] = _correct
_REPL_KEYS: List[str] = sorted(_REPL_MAP, key=len, reverse=True)
_REPL_RE: Optional["re.Pattern[str]"] = (
    re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(k) for k in _REPL_KEYS) + r")(?!\w)",
        re.UNICODE,
    )
    if _REPL_KEYS
    else None
)


def _repl_dispatch(m: "re.Match[str]") -> str:
    """يُرجع البديل القانوني للمصطلح المطابَق (ماركة/نوع)."""
    return _REPL_MAP.get(m.group(0), m.group(0))


# كلمات الضجيج التسويقي — من config مع احتياطي مستقل (يبقى الملف قابلاً
# للاستيراد منفرداً في الاختبارات). المفاتيح مطويّة كي تطابق النص المطويّ.
try:
    from config import CLOSED_LOOP_NOISE_WORDS as _RAW_NOISE
except Exception:  # noqa: BLE001
    _RAW_NOISE = [
        "tester", "تستر", "تيستر", "original", "اصلي", "اصلية",
        "authentic", "genuine", "new", "جديد", "offer", "عرض",
        "خصم", "تخفيض", "sale", "discount", "deal",
    ]
_NOISE_KEYS: List[str] = sorted(
    {_fold(w) for w in _RAW_NOISE if _fold(w)}, key=len, reverse=True
)
_NOISE_RE: Optional["re.Pattern[str]"] = (
    re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(k) for k in _NOISE_KEYS) + r")(?!\w)",
        re.UNICODE,
    )
    if _NOISE_KEYS
    else None
)

_SYMBOL_RE = re.compile(r"[^\w\s؀-ۿ]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


@lru_cache(maxsize=200_000)
def deep_clean(raw: str) -> str:
    """
    يُنتج إسقاطاً مطبَّعاً للاسم **للمطابقة فقط** — لا يمسّ الاسم الأصلي (Raw).

    تمريرة واحدة بطول الاسم، مع كاش يضرب بقوة لأن أسماء المنافسين تتكرّر
    عبر ٢٣ متجراً (صفر إعادة حساب). الخطوات:

    1. طيّ الأحرف العربية + التشكيل + توحيد الأرقام + خفض الحالة.
    2. تفكيك الرموز إلى مسافات + ضغط مبكّر (يحسّن مطابقة العبارات متعددة الكلمات).
    3. تطبيع الماركات والأنواع — تمريرة regex واحدة (بديل مُجمَّع + dispatch)
       بدل ٤٩ تمريرة في normalize_text القديمة.
    4. إزالة الضجيج التسويقي — **محمية (Guarded Noise)**: إذا أفرغ الضجيجُ
       الاسمَ بالكامل (مثل «تستر أصلي») تُعاد النسخة الأساس قبل الإزالة، فلا
       يُرجَع نصّ فارغ أبداً ولا يُشوَّه اسم قصير.

    Parameters
    ----------
    raw : str
        الاسم الخام. لا يُعدَّل.

    Returns
    -------
    str
        إسقاط مطبَّع غير فارغ (إلا إذا كان المُدخَل نفسه فارغاً/مسافات).
    """
    text = _fold(raw)
    text = _SYMBOL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if _REPL_RE is not None:
        text = _REPL_RE.sub(_repl_dispatch, text)
    base = _WS_RE.sub(" ", text).strip()          # النسخة الأساس (قبل الضجيج)

    if _NOISE_RE is None:
        return base
    cleaned = _WS_RE.sub(" ", _NOISE_RE.sub(" ", base)).strip()
    # 🛡️ حماية: لا نُرجع اسماً فارغاً بسبب الضجيج (الاسم كلّه كلمات تسويقية).
    return cleaned if cleaned else base


# ══════════════════════════════════════════════════════════════════════════════
#  2-bis.  مصفوفة القرار — decide  (الدائرة المقفلة v2.0)
#  العتبة تتحرّك بالاتفاق الهيكلي بدل خط مسطّح (درس Stage B: مطابقات حقيقية
#  بتشابه اسم منخفض ⇒ لا تُرمى في «مفقود»).
# ══════════════════════════════════════════════════════════════════════════════

try:
    from config import (  # noqa: E402
        CLOSED_LOOP_HIGH as _CL_HIGH,
        CLOSED_LOOP_REVIEW as _CL_REVIEW,
        CLOSED_LOOP_STRUCT_FLOOR as _CL_FLOOR,
    )
except Exception:  # noqa: BLE001
    _CL_HIGH, _CL_FLOOR, _CL_REVIEW = 80.0, 70.0, 60.0


class StructMatch(NamedTuple):
    """نتيجة الفحص الهيكلي لكل بُعد: ``agree`` | ``conflict`` | ``unknown``."""

    brand: str = "unknown"
    size: str = "unknown"
    type: str = "unknown"

    @property
    def has_conflict(self) -> bool:
        """تعارض مؤكد في أي بُعد (قيمتان موجودتان ومختلفتان)."""
        return "conflict" in (self.brand, self.size, self.type)

    @property
    def full_agree(self) -> bool:
        """اتفاق كامل في الأبعاد الثلاثة (أقوى دليل بنيوي)."""
        return self.brand == "agree" and self.size == "agree" and self.type == "agree"

    @property
    def has_signal(self) -> bool:
        """إشارة هيكلية إيجابية واحدة على الأقل."""
        return "agree" in (self.brand, self.size, self.type)


def fuse_scores(token_set: float, token_sort: float, partial: float) -> float:
    """يدمج مقاييس RapidFuzz في درجة واحدة 0-100 (قابلة للضبط).

    ``token_set``/``token_sort`` أساس المحاذاة اللفظية (يعالجان الكلمات الزائدة
    والمُعاد ترتيبها)، و``partial`` دعم ثانوي بوزن أقل لتفادي التضخيم الكاذب من
    المطابقات الجزئية.
    """
    base = max(float(token_set), float(token_sort))
    return round(base * 0.85 + float(partial) * 0.15, 2)


def decide(score_set: float, struct: StructMatch) -> str:
    """مصفوفة القرار للدائرة المقفلة — تُرجع إحدى الحالات الأربع.

    ============================  ==================  ====================
    الدرجة المُدمجة               الإشارة الهيكلية     القرار
    ============================  ==================  ====================
    أيّ قيمة                      تعارض في أي بُعد     ``STATUS_EXCLUDED``
    ≥ HIGH (80)                  بلا تعارض            ``STATUS_MATCHED``
    ≥ FLOOR (70)                 اتفاق كامل           ``STATUS_MATCHED``
    ≥ REVIEW (60)                بلا تعارض            ``STATUS_REVIEW`` → AI
    < REVIEW (60)                إشارة هيكلية         ``STATUS_REVIEW`` → AI
    < REVIEW (60)                بلا إشارة            ``STATUS_MISSING``
    ============================  ==================  ====================

    Parameters
    ----------
    score_set : float
        الدرجة المُدمجة (عادةً من ``fuse_scores``)، 0-100.
    struct : StructMatch
        نتيجة الفحص الهيكلي (ماركة/حجم/نوع).

    Returns
    -------
    str
        إحدى: ``STATUS_MATCHED`` / ``STATUS_REVIEW`` / ``STATUS_MISSING`` /
        ``STATUS_EXCLUDED``.
    """
    score = float(score_set)

    # تعارض هيكلي مؤكد يتغلّب على أي درجة — حتى 100%.
    if struct.has_conflict:
        return STATUS_EXCLUDED

    # درجة عالية بلا تعارض → مطابقة مؤكدة.
    if score >= _CL_HIGH:
        return STATUS_MATCHED

    # ‏70-80 لكن الهيكل متفق تماماً → الهيكل دليل كافٍ لرفع المطابقة.
    if score >= _CL_FLOOR and struct.full_agree:
        return STATUS_MATCHED

    # المنطقة الرمادية 60-80 → مراجعة بالذكاء الاصطناعي.
    if score >= _CL_REVIEW:
        return STATUS_REVIEW

    # أقل من 60 لكن مع إشارة هيكلية → مراجعة (درس Stage B: لا نرميها مفقودة).
    if struct.has_signal:
        return STATUS_REVIEW

    # أقل من 60 وبلا أي إشارة → مفقود مؤكد.
    return STATUS_MISSING


# ══════════════════════════════════════════════════════════════════════════════
#  2.  دوال الاستخراج الهيكلي
# ══════════════════════════════════════════════════════════════════════════════

def _extract_volume(normalized: str) -> Optional[str]:
    """يستخرج الحجم (مثل '100' من '100ml') من الاسم المطبَّع."""
    m = _VOLUME_RE.search(normalized)
    if m:
        return m.group(1).replace(",", ".")
    return None


def _extract_concentration_type(normalized: str) -> Optional[str]:
    """يستخرج نوع التركيز (edp/edt/edc/...) من الاسم المطبَّع."""
    m = _TYPE_RE.search(normalized)
    if m:
        raw_type = m.group(1).lower().strip()
        # توحيد المرادفات
        _aliases = {
            "eau de parfum": "edp", "بارفيم": "edp",
            "بارفام": "edp", "بارفان": "edp",
            "eau de toilette": "edt", "تواليت": "edt",
            "eau de cologne": "edc", "كولونيا": "edc",
        }
        return _aliases.get(raw_type, raw_type)
    return None


def _extract_brand(normalized: str, known_brands: List[str]) -> Optional[str]:
    """
    يستخرج اسم الماركة من الاسم المطبَّع بمطابقة القائمة المعروفة.
    يُفضّل الأطول لتجنب التعارض (مثل "Tom Ford" قبل "Ford").
    """
    name_lower = normalized.lower()
    for brand in sorted(known_brands, key=len, reverse=True):
        if re.search(
            r"(?<!\w)" + re.escape(brand.lower()) + r"(?!\w)",
            name_lower,
            re.UNICODE,
        ):
            return brand.lower()
    return None


def _check_structural_constraints(
    our_norm: str,
    comp_norm: str,
    known_brands: List[str],
) -> Tuple[bool, str]:
    """
    يتحقق من تطابق الهيكل الثلاثي: حجم + ماركة + نوع تركيز.

    القاعدة: يُعاقَب فقط عند وجود القيمة في الجانبين وعدم تطابقها.
    إذا غاب أحد الجانبين → محايد (لا يُعدّ خطأً).

    Parameters
    ----------
    our_norm : str
        الاسم المطبَّع لمنتجنا في الكتالوج.
    comp_norm : str
        الاسم المطبَّع لمنتج المنافس.
    known_brands : list[str]
        قائمة الماركات المعروفة.

    Returns
    -------
    tuple[bool, str]
        (all_constraints_pass, human_readable_reason)
    """
    violations: List[str] = []

    our_vol  = _extract_volume(our_norm)
    comp_vol = _extract_volume(comp_norm)
    if our_vol and comp_vol and our_vol != comp_vol:
        violations.append(f"حجم مختلف ({our_vol}ml != {comp_vol}ml)")

    our_type  = _extract_concentration_type(our_norm)
    comp_type = _extract_concentration_type(comp_norm)
    if our_type and comp_type and our_type != comp_type:
        violations.append(f"نوع مختلف ({our_type.upper()} != {comp_type.upper()})")

    our_brand  = _extract_brand(our_norm, known_brands)
    comp_brand = _extract_brand(comp_norm, known_brands)
    if our_brand and comp_brand and our_brand != comp_brand:
        violations.append(f"ماركة مختلفة ({our_brand} != {comp_brand})")

    if violations:
        return False, " | ".join(violations)
    return True, ""


def _build_struct_match(
    our_norm: str,
    comp_norm: str,
    known_brands: List[str],
) -> StructMatch:
    """
    يبني كائن ``StructMatch`` من الفحص الهيكلي الثلاثي (ماركة/حجم/نوع).

    يُستخدم مع ``decide()`` لتمكين مصفوفة القرار الذكية من حسم
    المنتجات في منطقة الشك المنخفضة (score < REVIEW_LOWER).

    Parameters
    ----------
    our_norm : str
        الاسم المطبَّع لمنتجنا.
    comp_norm : str
        الاسم المطبَّع لمنتج المنافس.
    known_brands : list[str]
        قائمة الماركات المعروفة.

    Returns
    -------
    StructMatch
        كائن يحمل حالة كل بُعد: ``agree`` / ``conflict`` / ``unknown``.
    """
    our_vol  = _extract_volume(our_norm)
    comp_vol = _extract_volume(comp_norm)
    if our_vol and comp_vol:
        size_state = "agree" if our_vol == comp_vol else "conflict"
    else:
        size_state = "unknown"

    our_type  = _extract_concentration_type(our_norm)
    comp_type = _extract_concentration_type(comp_norm)
    if our_type and comp_type:
        type_state = "agree" if our_type == comp_type else "conflict"
    else:
        type_state = "unknown"

    our_brand  = _extract_brand(our_norm, known_brands)
    comp_brand = _extract_brand(comp_norm, known_brands)
    if our_brand and comp_brand:
        brand_state = "agree" if our_brand == comp_brand else "conflict"
    else:
        brand_state = "unknown"

    return StructMatch(brand=brand_state, size=size_state, type=type_state)



# ══════════════════════════════════════════════════════════════════════════════
#  3.  بنّاء صف النتيجة — _build_result_row
# ══════════════════════════════════════════════════════════════════════════════

def _build_result_row(
    raw_name: str,
    normalized_name: str,
    comp_price: Optional[float],
    comp_label: str,
    *,
    status: str,
    match_score: float,
    match_reason: str,
    match_layer: int,
    our_no: Optional[str] = None,
    our_name: Optional[str] = None,
    our_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    يبني صفاً كاملاً وموحداً لجدول النتائج.

    كل صف يحمل بصمة القرار الكاملة (Match_Score + Match_Reason).
    لا يُرجع None أبداً — كل صف مكتمل.

    Parameters
    ----------
    raw_name : str
        الاسم الخام للمنتج من ملف المنافس (لا يُعدَّل).
    normalized_name : str
        الاسم المطبَّع (للعرض + التدقيق).
    comp_price : float | None
        سعر المنافس بعد التنظيف.
    comp_label : str
        اسم المنافس للعرض.
    status : str
        STATUS_MATCHED / STATUS_REVIEW / STATUS_MISSING
    match_score : float
        نسبة التطابق (0-100).
    match_reason : str
        سبب التصنيف بنص قابل للقراءة.
    match_layer : int
        رقم الطبقة التي أنتجت هذه النتيجة (1-4).
    our_no : str | None
        رقم المنتج No. في سلة (إذا وُجد مطابق أو مرشح).
    our_name : str | None
        اسم منتجنا في الكتالوج.
    our_price : float | None
        سعر منتجنا (يُستخدم للمقارنة عند MATCHED فقط).

    Returns
    -------
    dict
        صف جاهز للإضافة إلى قائمة النتائج.
    """
    price_diff: Optional[float] = None
    if (
        status == STATUS_MATCHED
        and our_price is not None
        and comp_price is not None
    ):
        price_diff = round(comp_price - our_price, 2)

    return {
        "Raw_Name":          raw_name,
        "Normalized_Name":   normalized_name,
        "سعر_المنافس":       comp_price,
        "المنافس":           comp_label,
        "No.":               our_no,
        "أسم_المنتج_لدينا":  our_name,
        "سعر_المنتج_لدينا":  our_price if status == STATUS_MATCHED else None,
        "Match_Score":       round(float(match_score), 2),
        "Match_Reason":      match_reason,
        "الحالة":            status,
        "طبقة_المطابقة":     match_layer,
        "فرق_السعر":         price_diff,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  4.  مسار الشلال الرباعي — _route_single_product
# ══════════════════════════════════════════════════════════════════════════════

def _route_single_product(
    raw_name: str,
    comp_price: Optional[float],
    comp_label: str,
    comp_sku: Optional[str],
    catalog_df: pd.DataFrame,
    catalog_norms: List[str],
    sku_index: Dict[str, int],
    exact_index: Dict[str, int],
    known_brands: List[str],
) -> Dict[str, Any]:
    """
    يُمرّر منتجاً واحداً عبر الشلال الرباعي ويُعيد صف نتيجة كاملاً.

    لا يُرجع None أبداً — كل منتج يخرج من طبقة بنتيجة محددة.

    Parameters
    ----------
    raw_name : str
        الاسم الخام من ملف المنافس.
    comp_price : float | None
        السعر المنظَّف.
    comp_label : str
        اسم المنافس.
    comp_sku : str | None
        SKU أو باركود المنافس (إن وُجد).
    catalog_df : pd.DataFrame
        كتالوج متجرنا (مطبَّع مسبقاً).
    catalog_norms : list[str]
        قائمة الأسماء المطبَّعة لكل منتج في الكتالوج (Pre-built).
    sku_index : dict[str, int]
        فهرس SKU → فهرس الصف في catalog_df.
    exact_index : dict[str, int]
        فهرس الاسم المطبَّع → فهرس الصف في catalog_df.
    known_brands : list[str]
        قائمة الماركات لفحص الهيكل.

    Returns
    -------
    dict
        صف نتيجة مكتمل يُضاف للقائمة النهائية.
    """
    comp_norm: str = normalize_text(raw_name)

    # ──────────────────────────────────────────────────────────────────────
    #  الطبقة 1: مطابقة SKU / باركود (تطابق قطعي 100%)
    # ──────────────────────────────────────────────────────────────────────
    if comp_sku and sku_index:
        sku_clean = str(comp_sku).strip()
        if sku_clean and sku_clean.lower() not in ("nan", "none", ""):
            catalog_idx = sku_index.get(sku_clean)
            if catalog_idx is not None:
                cat = catalog_df.iloc[catalog_idx]
                return _build_result_row(
                    raw_name, comp_norm, comp_price, comp_label,
                    status=STATUS_MATCHED,
                    match_score=100.0,
                    match_reason="طبقة 1: مطابقة قطعية عبر SKU/باركود (100%)",
                    match_layer=1,
                    our_no=str(cat[CAT_PK]),
                    our_name=str(cat[CAT_NAME]),
                    our_price=cat[CAT_PRICE],
                )

    # ──────────────────────────────────────────────────────────────────────
    #  الطبقة 2: مطابقة نصية تامة بعد التطبيع (تطابق 100%)
    # ──────────────────────────────────────────────────────────────────────
    if comp_norm and comp_norm in exact_index:
        catalog_idx = exact_index[comp_norm]
        cat = catalog_df.iloc[catalog_idx]
        return _build_result_row(
            raw_name, comp_norm, comp_price, comp_label,
            status=STATUS_MATCHED,
            match_score=100.0,
            match_reason="طبقة 2: مطابقة نصية تامة بعد تطبيع الأحرف",
            match_layer=2,
            our_no=str(cat[CAT_PK]),
            our_name=str(cat[CAT_NAME]),
            our_price=cat[CAT_PRICE],
        )

    # ──────────────────────────────────────────────────────────────────────
    #  الطبقة 3: RapidFuzz + تحقق هيكلي (حجم + ماركة + نوع)
    # ──────────────────────────────────────────────────────────────────────
    if catalog_norms:
        # نحصل على أفضل مطابقة بدون score_cutoff لضمان الحصول على بصمة قرار دائماً
        best_match = rf_process.extractOne(
            comp_norm,
            catalog_norms,
            scorer=fuzz.token_set_ratio,
        )

        if best_match is not None:
            _best_str, score, catalog_idx = best_match
            score = float(score)
            cat = catalog_df.iloc[catalog_idx]
            our_norm_str = catalog_norms[catalog_idx]
            our_no   = str(cat[CAT_PK])
            our_name = str(cat[CAT_NAME])
            our_price = cat[CAT_PRICE]

            if score >= CONFIRMED_SCORE:
                # نسبة عالية — نتحقق من الهيكل
                struct_ok, struct_detail = _check_structural_constraints(
                    our_norm_str, comp_norm, known_brands
                )
                if struct_ok:
                    return _build_result_row(
                        raw_name, comp_norm, comp_price, comp_label,
                        status=STATUS_MATCHED,
                        match_score=score,
                        match_reason=(
                            f"طبقة 3: تطابق فازي مؤكد {score:.1f}% "
                            f"+ هيكل مطابق (حجم+ماركة+نوع)"
                        ),
                        match_layer=3,
                        our_no=our_no,
                        our_name=our_name,
                        our_price=our_price,
                    )
                else:
                    # نسبة عالية لكن الهيكل يختلف → مراجعة يدوية
                    return _build_result_row(
                        raw_name, comp_norm, comp_price, comp_label,
                        status=STATUS_REVIEW,
                        match_score=score,
                        match_reason=(
                            f"طبقة 3: نسبة {score:.1f}% عالية لكن اختلاف هيكلي — "
                            f"{struct_detail}"
                        ),
                        match_layer=3,
                        our_no=our_no,
                        our_name=our_name,
                        our_price=None,
                    )

            elif score >= REVIEW_LOWER:
                # المنطقة الرمادية 65-89% → مراجعة يدوية (حظر الاجتهاد)
                return _build_result_row(
                    raw_name, comp_norm, comp_price, comp_label,
                    status=STATUS_REVIEW,
                    match_score=score,
                    match_reason=(
                        f"طبقة 3: المنطقة الرمادية {score:.1f}% "
                        f"(65-89%) — مراجعة يدوية إلزامية"
                    ),
                    match_layer=3,
                    our_no=our_no,
                    our_name=our_name,
                    our_price=None,
                )

            else:
                # نسبة < 65% → نستخدم decide() مع الفحص الهيكلي لحسم الأمر
                # بدلاً من التصنيف الأعمى كمفقود
                struct = _build_struct_match(
                    our_norm_str, comp_norm, known_brands
                )
                final_status = decide(score, struct)

                if final_status == STATUS_EXCLUDED:
                    # تعارض هيكلي مؤكد → مستبعد
                    return _build_result_row(
                        raw_name, comp_norm, comp_price, comp_label,
                        status=STATUS_EXCLUDED,
                        match_score=score,
                        match_reason=(
                            f"طبقة 3: نسبة {score:.1f}% مع تعارض هيكلي — مستبعد"
                        ),
                        match_layer=3,
                        our_no=our_no,
                        our_name=f"[تعارض] {our_name}",
                        our_price=None,
                    )
                elif final_status == STATUS_REVIEW:
                    # إشارة هيكلية إيجابية → مراجعة بدلاً من مفقود
                    return _build_result_row(
                        raw_name, comp_norm, comp_price, comp_label,
                        status=STATUS_REVIEW,
                        match_score=score,
                        match_reason=(
                            f"طبقة 3: نسبة {score:.1f}% منخفضة لكن إشارة "
                            f"هيكلية إيجابية — مراجعة AI إلزامية"
                        ),
                        match_layer=3,
                        our_no=our_no,
                        our_name=f"[مرشح ضعيف] {our_name}",
                        our_price=None,
                    )
                else:
                    # مفقود مؤكد: score < 60 وبلا أي إشارة هيكلية
                    return _build_result_row(
                        raw_name, comp_norm, comp_price, comp_label,
                        status=STATUS_MISSING,
                        match_score=score,
                        match_reason=(
                            f"طبقة 3: نسبة {score:.1f}% أقل من الحد "
                            f"وبلا إشارة هيكلية — مفقود مؤكد"
                        ),
                        match_layer=3,
                        our_no=our_no,
                        our_name=f"[مرشح ضعيف] {our_name}",
                        our_price=None,
                    )

    # ──────────────────────────────────────────────────────────────────────
    #  الطبقة 4: شبكة الأمان — لا يسقط حرف واحد
    #  تصل هنا فقط إذا كان الكتالوج فارغاً تماماً أو extractOne أعاد None
    # ──────────────────────────────────────────────────────────────────────
    return _build_result_row(
        raw_name, comp_norm, comp_price, comp_label,
        status=STATUS_REVIEW,
        match_score=0.0,
        match_reason=(
            "طبقة 4: شبكة الأمان — لم يُعثر على أي مرشح — مراجعة يدوية إلزامية"
        ),
        match_layer=4,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  5.  تنظيف الأسعار (نفس clean_price من pricing_engine — مستقل)
# ══════════════════════════════════════════════════════════════════════════════

def _clean_price(raw: Any) -> Optional[float]:
    """
    يحوّل أي تمثيل سعر إلى float آمن.
    يعالج: "150 ر.س" / "1,250.50" / NaN / None / نص فارغ → None.

    Parameters
    ----------
    raw : Any
        القيمة الخام من الخلية.

    Returns
    -------
    float | None
    """
    if raw is None:
        return None
    try:
        val = float(raw)
        return None if pd.isna(val) else val
    except (ValueError, TypeError):
        pass
    try:
        text = str(raw).strip()
        if not text or text.lower() in ("nan", "none", "-", ""):
            return None
        digits_only = re.sub(r"[^\d.,]", "", text).replace(",", "")
        return float(digits_only) if digits_only else None
    except Exception:  # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  6.  وحدة التحقق من الدائرة المغلقة — _sanity_check
# ══════════════════════════════════════════════════════════════════════════════

def _sanity_check(
    n_input: int,
    n_matched: int,
    n_review: int,
    n_missing: int,
    competitor_name: str = "غير محدد",
) -> None:
    """
    يتحقق من قانون حفظ البيانات:
        المدخل = المتطابق + المراجعة + المفقود

    إذا كان المجموع مختلفاً → RuntimeError فوري.
    لا تسامح مطلق — صفر تسرب مقبول.

    Parameters
    ----------
    n_input : int
        عدد منتجات المنافس المُدخلة.
    n_matched : int
        عدد المتطابقات المؤكدة.
    n_review : int
        عدد المنتجات في قسم المراجعة اليدوية.
    n_missing : int
        عدد المنتجات المفقودة.
    competitor_name : str
        اسم المنافس (للتشخيص في رسالة الخطأ).

    Raises
    ------
    RuntimeError
        إذا كان المجموع لا يساوي المدخل — انتهاك قانون حفظ البيانات.
    """
    n_output = n_matched + n_review + n_missing
    if n_output != n_input:
        delta = n_input - n_output
        raise RuntimeError(
            f"\n{'═' * 60}\n"
            f"❌  انتهاك قانون حفظ البيانات (الدائرة المغلقة)!\n"
            f"    المنافس:   {competitor_name}\n"
            f"    المدخل:    {n_input:,} منتج\n"
            f"    المخرج:    {n_output:,} منتج\n"
            f"             (متطابق:{n_matched} + مراجعة:{n_review} + مفقود:{n_missing})\n"
            f"    الفجوة:    {abs(delta):,} منتج {'مفقود في العملية' if delta > 0 else 'زائد بشكل غير مبرر'}\n"
            f"{'═' * 60}"
        )
    logger.info(
        "✅ تحقق الدائرة المغلقة [%s]: %d = %d+%d+%d",
        competitor_name, n_input, n_matched, n_review, n_missing,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  7.  الفهرسة المسبقة للكتالوج — _build_catalog_indexes
# ══════════════════════════════════════════════════════════════════════════════

def _build_catalog_indexes(
    catalog_df: pd.DataFrame,
    sku_col: Optional[str] = None,
) -> Tuple[List[str], Dict[str, int], Dict[str, int]]:
    """
    يبني الفهارس المسبقة للكتالوج مرة واحدة لكل المنافسين.

    Parameters
    ----------
    catalog_df : pd.DataFrame
        كتالوج متجرنا المنظَّف.
    sku_col : str | None
        اسم عمود الـ SKU في الكتالوج (إن وُجد).

    Returns
    -------
    tuple[list[str], dict[str,int], dict[str,int]]
        (catalog_norms, sku_index, exact_index)
        - catalog_norms: قائمة الأسماء المطبَّعة (للـ Fuzzy)
        - sku_index:     SKU string → row index
        - exact_index:   normalized name → row index
    """
    logger.info("🔨 بناء فهارس الكتالوج (%d منتج) ...", len(catalog_df))
    t0 = time.perf_counter()

    catalog_norms: List[str] = [
        normalize_text(n) for n in catalog_df[CAT_NAME].fillna("")
    ]

    exact_index: Dict[str, int] = {}
    for idx, norm in enumerate(catalog_norms):
        if norm and norm not in exact_index:
            exact_index[norm] = idx

    sku_index: Dict[str, int] = {}
    if sku_col and sku_col in catalog_df.columns:
        for idx, sku_val in enumerate(catalog_df[sku_col].astype(str)):
            sku_clean = sku_val.strip()
            if sku_clean and sku_clean.lower() not in ("nan", "none", ""):
                if sku_clean not in sku_index:
                    sku_index[sku_clean] = idx

    elapsed = time.perf_counter() - t0
    logger.info(
        "✅ الفهارس جاهزة: exact=%d | sku=%d (%.2f ث)",
        len(exact_index), len(sku_index), elapsed,
    )
    return catalog_norms, sku_index, exact_index


# ══════════════════════════════════════════════════════════════════════════════
#  8.  تنظيف وتحقق من كتالوج المتجر
# ══════════════════════════════════════════════════════════════════════════════

def _prepare_catalog(
    catalog_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    يتحقق من الأعمدة الإلزامية ويُنظّف الكتالوج بدون dropna().

    القواعد:
    - صفوف No. الفارغة → تُنقل إلى جدول تحذير وتُسجَّل ثم تُحذف.
    - الأسعار تُنظَّف بـ _clean_price().
    - لا يُسمح بـ dropna() العشوائي.

    Parameters
    ----------
    catalog_df : pd.DataFrame
        الكتالوج كما رُفع.

    Returns
    -------
    pd.DataFrame
        الكتالوج المنظَّف.

    Raises
    ------
    KeyError
        إذا غابت أعمدة إلزامية.
    """
    required = {CAT_PK, CAT_NAME, CAT_PRICE}
    missing_cols = required - set(catalog_df.columns)
    if missing_cols:
        raise KeyError(
            f"❌ الأعمدة الإلزامية التالية غير موجودة: {missing_cols}\n"
            f"الأعمدة المتاحة: {list(catalog_df.columns)}"
        )

    df = catalog_df[[CAT_PK, CAT_NAME, CAT_PRICE]].copy()
    df[CAT_PK] = df[CAT_PK].astype(str).str.strip()

    # تحديد الصفوف غير الصالحة بدقة (لا dropna عشوائي)
    invalid_pk = (
        df[CAT_PK].isna()
        | (df[CAT_PK] == "")
        | (df[CAT_PK].str.lower() == "nan")
        | (df[CAT_PK].str.lower() == "none")
    )
    n_dropped = int(invalid_pk.sum())
    if n_dropped > 0:
        logger.warning(
            "⚠️ تجاهل %d صف من الكتالوج بسبب No. فارغ أو غير صالح.",
            n_dropped,
        )

    df = df[~invalid_pk].copy()
    df[CAT_NAME]  = df[CAT_NAME].astype(str).str.strip()
    df[CAT_PRICE] = df[CAT_PRICE].apply(_clean_price)

    logger.info("✅ الكتالوج جاهز: %d منتج (%d صف مُسقط).", len(df), n_dropped)
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
#  9.  التقسيم الرباعي للنتائج — _split_results
# ══════════════════════════════════════════════════════════════════════════════

def _split_results(
    results_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    يقسّم جدول النتائج الكامل إلى 4 DataFrames مستقلة.

    القسم 1 — matched_cheaper:   متطابق + سعر المنافس < سعرنا
    القسم 2 — matched_pricier:   متطابق + سعر المنافس >= سعرنا
    القسم 3 — review:            مراجعة يدوية (المنطقة الرمادية)
    القسم 4 — missing:           مفقودات (score < 65%)

    Parameters
    ----------
    results_df : pd.DataFrame
        جدول نتائج كامل من run_closed_loop_matching().

    Returns
    -------
    tuple[DataFrame, DataFrame, DataFrame, DataFrame]
    """
    matched_mask = results_df["الحالة"] == STATUS_MATCHED
    review_mask  = results_df["الحالة"] == STATUS_REVIEW
    # EXCLUDED (تعارض هيكلي) يُدمَج مع المفقود كي لا يُسقط صامتاً من الأقسام الأربعة.
    missing_mask = results_df["الحالة"].isin([STATUS_MISSING, STATUS_EXCLUDED])

    matched_df = results_df[matched_mask].copy()
    review_df  = results_df[review_mask].copy().reset_index(drop=True)
    missing_df = results_df[missing_mask].copy().reset_index(drop=True)

    # تقسيم المتطابقين حسب السعر (Vectorized)
    both_prices = (
        matched_df["سعر_المنافس"].notna()
        & matched_df["سعر_المنتج_لدينا"].notna()
    )
    valid_matched    = matched_df[both_prices]
    no_price_matched = matched_df[~both_prices]

    cheaper_mask = valid_matched["فرق_السعر"] < 0
    matched_cheaper = valid_matched[cheaper_mask].copy().reset_index(drop=True)
    matched_pricier = pd.concat(
        [valid_matched[~cheaper_mask], no_price_matched],
        ignore_index=True,
    )

    return matched_cheaper, matched_pricier, review_df, missing_df


# ══════════════════════════════════════════════════════════════════════════════
#  10. الدالة الرئيسية — run_closed_loop_matching
# ══════════════════════════════════════════════════════════════════════════════

def run_closed_loop_matching(
    catalog_df: pd.DataFrame,
    competitor_entries: List[Dict[str, Any]],
    known_brands: Optional[List[str]] = None,
    catalog_sku_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    المحرك الرئيسي للمطابقة الدائرية المغلقة.

    ⚠️ للتحليل والعرض فقط — لا يُعدِّل أي أسعار ولا يتصل بأي API ⚠️

    Parameters
    ----------
    catalog_df : pd.DataFrame
        كتالوج متجرنا (يجب أن يحتوي على: No. / أسم المنتج / سعر المنتج).
    competitor_entries : list[dict]
        قائمة المنافسين. كل عنصر::

            {
                "df":              pd.DataFrame,   # DataFrame ملف المنافس
                "name_col":        str,             # عمود أسماء المنتجات
                "price_col":       str,             # عمود الأسعار
                "competitor_name": str,             # الاسم التعريفي
                "sku_col":         str | None,      # عمود SKU/باركود (اختياري)
            }

    known_brands : list[str] | None
        قائمة الماركات المعروفة للتحقق الهيكلي.
        إذا تُرك None يُحاول الاستيراد من config.KNOWN_BRANDS.
    catalog_sku_col : str | None
        اسم عمود SKU/باركود في كتالوج المتجر (اختياري).

    Returns
    -------
    dict
        ``"matched_cheaper"``  → DataFrame المتطابقات حيث المنافس أرخص
        ``"matched_pricier"``  → DataFrame المتطابقات حيث المنافس أغلى
        ``"review"``           → DataFrame المنطقة الرمادية (مراجعة يدوية)
        ``"missing"``          → DataFrame المفقودات (< 65%)
        ``"all_results"``      → DataFrame الكامل (لـ Audit)
        ``"summary"``          → dict إحصائيات الدائرة المغلقة
        ``"sanity_passed"``    → bool نجاح تحقق الدائرة المغلقة

    Raises
    ------
    KeyError
        إذا غابت أعمدة إلزامية من الكتالوج أو أي ملف منافس.
    RuntimeError
        إذا انتُهك قانون حفظ البيانات (تسرب أي منتج).
    ValueError
        إذا كانت قائمة competitor_entries فارغة.
    """
    logger.info("═" * 65)
    logger.info("🚀 بدء المحرك الدائري المغلق")
    logger.info("═" * 65)

    if not competitor_entries:
        raise ValueError("❌ يجب توفير ملف منافس واحد على الأقل.")

    # استيراد قائمة الماركات من config إذا لم تُعطَ
    if known_brands is None:
        try:
            from config import KNOWN_BRANDS as _kb
            known_brands = list(_kb)
        except ImportError:
            known_brands = []
            logger.warning("⚠️ لم يُعثر على config.KNOWN_BRANDS — التحقق الهيكلي بدون قائمة ماركات.")

    # ── تنظيف وفهرسة الكتالوج (مرة واحدة لكل المنافسين) ──────────────────
    catalog_clean = _prepare_catalog(catalog_df)

    if catalog_clean.empty:
        raise ValueError("❌ الكتالوج فارغ بعد التنظيف — لا يمكن الاستمرار.")

    catalog_norms, sku_index, exact_index = _build_catalog_indexes(
        catalog_clean, sku_col=catalog_sku_col
    )

    # ── معالجة كل منافس ───────────────────────────────────────────────────
    all_rows: List[Dict[str, Any]] = []
    per_competitor_stats: List[Dict[str, Any]] = []

    for entry in competitor_entries:
        raw_comp_df: Optional[pd.DataFrame] = entry.get("df")
        name_col:    str                    = entry.get("name_col", "")
        price_col:   str                    = entry.get("price_col", "")
        comp_name:   str                    = entry.get("competitor_name", "منافس")
        sku_col_comp: Optional[str]         = entry.get("sku_col")

        if raw_comp_df is None or raw_comp_df.empty:
            logger.warning("⚠️ DataFrame '%s' فارغ — تم تخطيه.", comp_name)
            continue

        if not name_col or not price_col:
            logger.warning("⚠️ أعمدة '%s' غير محددة — تم تخطيه.", comp_name)
            continue

        # تحقق من الأعمدة المطلوبة
        missing_entry_cols = {name_col, price_col} - set(raw_comp_df.columns)
        if missing_entry_cols:
            raise KeyError(
                f"❌ الأعمدة {missing_entry_cols} غير موجودة في ملف '{comp_name}'.\n"
                f"الأعمدة المتاحة: {list(raw_comp_df.columns)}"
            )

        logger.info("🔍 معالجة '%s': %d منتج", comp_name, len(raw_comp_df))
        t_start = time.perf_counter()

        comp_rows: List[Dict[str, Any]] = []

        # ⚡ PERFORMANCE FIX: استبدال iterrows البطيء بـ to_dict('records')
        for comp_row in raw_comp_df.to_dict('records'):
            raw_name_val: str          = str(comp_row.get(name_col, "")).strip()
            comp_price_val             = _clean_price(comp_row.get(price_col))
            comp_sku_val: Optional[str] = (
                str(comp_row.get(sku_col_comp, "")).strip()
                if sku_col_comp and sku_col_comp in comp_row
                else None
            )

            # كل صف يمر عبر الشلال — لا استثناء مسكوت بصمت
            result_row = _route_single_product(
                raw_name=raw_name_val,
                comp_price=comp_price_val,
                comp_label=comp_name,
                comp_sku=comp_sku_val,
                catalog_df=catalog_clean,
                catalog_norms=catalog_norms,
                sku_index=sku_index,
                exact_index=exact_index,
                known_brands=known_brands,
            )
            comp_rows.append(result_row)

        # ── التحقق من الدائرة المغلقة لكل منافس ──────────────────────────
        n_in       = len(raw_comp_df)
        n_matched  = sum(1 for r in comp_rows if r["الحالة"] == STATUS_MATCHED)
        n_review   = sum(1 for r in comp_rows if r["الحالة"] == STATUS_REVIEW)
        n_missing  = sum(1 for r in comp_rows if r["الحالة"] == STATUS_MISSING)
        n_excluded = sum(1 for r in comp_rows if r["الحالة"] == STATUS_EXCLUDED)

        # EXCLUDED يُحسب مع المفقود في التحقق وإلا اختلّ التوازن → RuntimeError
        _sanity_check(n_in, n_matched, n_review, n_missing + n_excluded, comp_name)

        elapsed = time.perf_counter() - t_start
        logger.info(
            "✅ '%s' انتهى في %.2f ث — متطابق:%d | مراجعة:%d | مفقود:%d",
            comp_name, elapsed, n_matched, n_review, n_missing,
        )

        per_competitor_stats.append({
            "المنافس":        comp_name,
            "إجمالي_المدخل": n_in,
            "متطابق":        n_matched,
            "مراجعة":        n_review,
            "مفقود":         n_missing,
        })

        all_rows.extend(comp_rows)

    if not all_rows:
        logger.warning("⚠️ لم تُنتج أي نتيجة — جميع المنافسين تم تخطيهم.")
        _empty = pd.DataFrame()
        return {
            "matched_cheaper": _empty,
            "matched_pricier": _empty,
            "review":          _empty,
            "missing":         _empty,
            "all_results":     _empty,
            "summary":         {"خطأ": "لا نتائج"},
            "sanity_passed":   False,
            "per_competitor":  per_competitor_stats,
        }

    # ── التحقق الشامل للدائرة المغلقة (كل المنافسين مجتمعين) ─────────────
    all_df     = pd.DataFrame(all_rows)
    total_in   = len(all_df)
    total_mat  = int((all_df["الحالة"] == STATUS_MATCHED).sum())
    total_rev  = int((all_df["الحالة"] == STATUS_REVIEW).sum())
    total_mis  = int((all_df["الحالة"] == STATUS_MISSING).sum())
    total_exc  = int((all_df["الحالة"] == STATUS_EXCLUDED).sum())

    _sanity_check(total_in, total_mat, total_rev, total_mis + total_exc, "الإجمالي الكامل")

    # ── التقسيم الرباعي ──────────────────────────────────────────────────
    cheaper_df, pricier_df, review_df, missing_df = _split_results(all_df)

    # ── بناء الملخص ──────────────────────────────────────────────────────
    summary: Dict[str, Any] = {
        "إجمالي_مدخل":        total_in,
        "متطابق_مؤكد":        total_mat,
        "مراجعة_يدوية":       total_rev,
        "مفقود":              total_mis,
        "منافس_أقل_سعراً":    len(cheaper_df),
        "منافس_أعلى_أو_مساوٍ": len(pricier_df),
        "الدائرة_المغلقة":    f"{total_in} = {total_mat}+{total_rev}+{total_mis} ✅",
    }

    logger.info("─" * 65)
    logger.info("📊 الملخص الكامل:")
    for k, v in summary.items():
        logger.info("    %-36s: %s", k, v)
    logger.info("─" * 65)
    logger.info("✅ المحرك الدائري المغلق اكتمل بنجاح.")

    return {
        "matched_cheaper": cheaper_df,
        "matched_pricier": pricier_df,
        "review":          review_df,
        "missing":         missing_df,
        "all_results":     all_df,
        "summary":         summary,
        "sanity_passed":   True,
        "per_competitor":  per_competitor_stats,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  11. السجل المحاسبي القائم على الهوية — CompetitorLedger  (الدائرة المقفلة v2.0)
#  «المحاسبة الكمومية»: كل منتج منافس يأخذ هوية فريدة (routing_id) وحالة طرفية
#  واحدة لا غير. القفل النهائي يتحقّق بالمجموعات (Set) لا بالعدّ — فيكشف حتى
#  «إسقاط + تكرار متوازنَين». أي يتيم بلا حالة ⇒ RuntimeError يذكر هويّته بالضبط.
# ══════════════════════════════════════════════════════════════════════════════

RoutingID = Tuple[str, int]
"""هوية فريدة لمنتج منافس واحد: (اسم المتجر، فهرس الصف)."""


def make_routing_id(store_name: Any, row_index: Any) -> RoutingID:
    """يبني هوية توجيه ثابتة وفريدة لمنتج منافس واحد."""
    return (str(store_name), int(row_index))


class LedgerState(str, Enum):
    """الحالات الطرفية الأربع — لكل منتج منافس واحدة منها حصراً."""

    CONFIRMED_MATCH   = "CONFIRMED_MATCH"     # متطابق مؤكد
    CONFIRMED_MISSING = "CONFIRMED_MISSING"   # مفقود مؤكد
    AI_REVIEW_PENDING = "AI_REVIEW_PENDING"   # بانتظار مراجعة الذكاء الاصطناعي
    EXCLUDED_NOISE    = "EXCLUDED_NOISE"      # مستبعد (تعارض هيكلي/ضجيج)


# جسر من مخرجات decide()/الشلال (حالات STATUS_*) إلى حالات السجل الأربع.
_STATUS_TO_STATE: Dict[str, "LedgerState"] = {
    STATUS_MATCHED:  LedgerState.CONFIRMED_MATCH,
    STATUS_MISSING:  LedgerState.CONFIRMED_MISSING,
    STATUS_REVIEW:   LedgerState.AI_REVIEW_PENDING,
    STATUS_EXCLUDED: LedgerState.EXCLUDED_NOISE,
}


def to_ledger_state(value: Any) -> "LedgerState":
    """يحوّل قيمة (LedgerState | حالة STATUS_* | اسم/قيمة العضو) إلى LedgerState."""
    if isinstance(value, LedgerState):
        return value
    if value in _STATUS_TO_STATE:
        return _STATUS_TO_STATE[value]
    try:
        return LedgerState(value)            # بالقيمة
    except ValueError:
        pass
    try:
        return LedgerState[value]            # بالاسم
    except (KeyError, TypeError):
        pass
    raise ValueError(f"حالة سجل غير معروفة: {value!r}")


class LedgerReport(NamedTuple):
    """ملخّص محاسبي بعد اجتياز القفل الكمومي."""

    total_input:       int
    confirmed_match:   int
    confirmed_missing: int
    ai_review_pending: int
    excluded_noise:    int
    balanced:          bool

    @property
    def assigned(self) -> int:
        return (
            self.confirmed_match + self.confirmed_missing
            + self.ai_review_pending + self.excluded_noise
        )


class CompetitorLedger:
    """
    سجلّ محاسبي صارم قائم على الهوية لمنتجات المنافسين.

    العقد:
    - ``register`` يُدخل هوية فريدة **مرة واحدة فقط** (تكرار ⇒ RuntimeError).
    - ``assign`` يُسند **حالة طرفية واحدة** لهوية مُسجَّلة (إسناد ثانٍ أو لحالة
      مختلفة ⇒ RuntimeError ؛ هوية غير مُسجَّلة ⇒ RuntimeError).
    - ``assert_closed_loop`` يتحقّق: مجموعة المُسنَدة == مجموعة المُدخلة (بالهوية
      لا بالعدّ). أي يتيم ⇒ RuntimeError يذكر الـ routing_id بالضبط.

    كل العمليات O(N) (مجموعات/تجزئة) — لا تُبطئ النظام حتى عند 129K منتج.
    """

    __slots__ = ("_inputs", "_assignments")

    def __init__(self) -> None:
        self._inputs: set = set()
        self._assignments: Dict[RoutingID, LedgerState] = {}

    # ── الإدخال ──────────────────────────────────────────────────────────
    def register(self, routing_id: RoutingID) -> None:
        """يُسجّل هوية منتج منافس واحدة. التكرار ممنوع منعاً باتاً."""
        if routing_id in self._inputs:
            raise RuntimeError(
                f"❌ تسجيل مكرّر لنفس الهوية في السجل: {routing_id!r} "
                f"(كل منتج منافس يُسجَّل مرة واحدة فقط)."
            )
        self._inputs.add(routing_id)

    def register_inputs(self, competitor_source: Any) -> int:
        """
        يُسجّل كل منتجات المنافسين دفعة واحدة من المصدر الخام.

        يقبل شكلين:
        - ``dict``: ``{اسم المتجر: rows}`` حيث ``rows`` له ``len`` (DataFrame/قائمة).
        - ``list[dict]``: عناصر ``run_closed_loop_matching`` (``df`` + ``competitor_name``).

        Returns
        -------
        int
            عدد الهويات المُسجَّلة حديثاً.
        """
        before = len(self._inputs)
        if isinstance(competitor_source, dict):
            for store, rows in competitor_source.items():
                n = 0 if rows is None else len(rows)
                for i in range(n):
                    self.register(make_routing_id(store, i))
        else:
            for entry in competitor_source:
                store = entry.get("competitor_name") or entry.get("المنافس") or "منافس"
                rows = entry.get("df")
                n = 0 if rows is None else len(rows)
                for i in range(n):
                    self.register(make_routing_id(store, i))
        return len(self._inputs) - before

    def register_all(self, routing_ids: Any) -> int:
        """يُسجّل مجموعة هويات جاهزة مباشرة (مثل معرّفات قاعدة المنافسين)."""
        before = len(self._inputs)
        for rid in routing_ids:
            self.register(rid)
        return len(self._inputs) - before

    # ── التخصيص ──────────────────────────────────────────────────────────
    def assign(self, routing_id: RoutingID, state: Any) -> None:
        """
        يُسند حالة طرفية لهوية مُسجَّلة — حالة واحدة لا غير.

        Raises
        ------
        RuntimeError
            إن كانت الهوية غير مُسجَّلة، أو مُسنَدة من قبل (منع حالتين).
        """
        new_state = to_ledger_state(state)
        if routing_id not in self._inputs:
            raise RuntimeError(
                f"❌ محاولة تخصيص هوية غير مُسجَّلة في السجل: {routing_id!r} "
                f"(لا يجوز تخصيص منتج لم يدخل أصلاً)."
            )
        existing = self._assignments.get(routing_id)
        if existing is not None:
            raise RuntimeError(
                f"❌ تخصيص مزدوج! الهوية {routing_id!r} مُسنَدة مسبقاً إلى "
                f"«{existing.value}» ومحاولة إسنادها إلى «{new_state.value}» — "
                f"كل منتج في حالة واحدة فقط."
            )
        self._assignments[routing_id] = new_state

    def assign_many(self, items: Any) -> int:
        """يُسند دفعة من أزواج (routing_id, state). يُعيد عدد ما أُسند."""
        n = 0
        pairs = items.items() if isinstance(items, dict) else items
        for routing_id, state in pairs:
            self.assign(routing_id, state)
            n += 1
        return n

    # ── القفل الكمومي ────────────────────────────────────────────────────
    def assert_closed_loop(self, *, context: str = "") -> LedgerReport:
        """
        القفل النهائي — يتحقّق من قانون حفظ البيانات بالهوية لا بالعدّ.

        Raises
        ------
        RuntimeError
            إذا بقيت أي هوية بلا حالة (يتيمة)، أو ظهرت حالة لهوية غير مُسجَّلة
            (شبح — دفاعي). الرسالة تذكر الـ routing_id بالضبط.

        Returns
        -------
        LedgerReport
            ملخّص متوازن عند النجاح.
        """
        assigned_ids = set(self._assignments)
        orphans = self._inputs - assigned_ids            # دخل بلا حالة
        phantoms = assigned_ids - self._inputs           # حالة بلا إدخال (دفاعي)

        if orphans or phantoms:
            label = f" [{context}]" if context else ""
            sample_o = sorted(map(repr, orphans))[:20]
            sample_p = sorted(map(repr, phantoms))[:20]
            raise RuntimeError(
                f"\n{'═' * 64}\n"
                f"❌  انتهاك قانون حفظ البيانات (السجل الكمومي){label}!\n"
                f"    المُدخَل:    {len(self._inputs):,}\n"
                f"    المُخصَّص:   {len(assigned_ids):,}\n"
                f"    يتامى (بلا حالة): {len(orphans):,}\n"
                f"      ↳ {sample_o}{' …' if len(orphans) > 20 else ''}\n"
                f"    أشباح (حالة بلا إدخال): {len(phantoms):,}\n"
                f"      ↳ {sample_p}{' …' if len(phantoms) > 20 else ''}\n"
                f"{'═' * 64}"
            )

        counts = Counter(self._assignments.values())
        report = LedgerReport(
            total_input=len(self._inputs),
            confirmed_match=counts.get(LedgerState.CONFIRMED_MATCH, 0),
            confirmed_missing=counts.get(LedgerState.CONFIRMED_MISSING, 0),
            ai_review_pending=counts.get(LedgerState.AI_REVIEW_PENDING, 0),
            excluded_noise=counts.get(LedgerState.EXCLUDED_NOISE, 0),
            balanced=True,
        )
        # تحقّق عددي إضافي (يجب أن يكون صحيحاً بالضرورة بعد فحص الهوية).
        if report.assigned != report.total_input:
            raise RuntimeError(
                f"❌ خلل عددي داخلي في السجل: assigned={report.assigned} "
                f"!= input={report.total_input}"
            )
        logger.info(
            "✅ السجل الكمومي متوازن%s: %d = %d+%d+%d+%d",
            f" [{context}]" if context else "",
            report.total_input, report.confirmed_match, report.confirmed_missing,
            report.ai_review_pending, report.excluded_noise,
        )
        return report

    # ── أدوات تشخيص ──────────────────────────────────────────────────────
    @property
    def n_input(self) -> int:
        return len(self._inputs)

    @property
    def n_assigned(self) -> int:
        return len(self._assignments)

    def unassigned(self) -> set:
        """يُعيد مجموعة الهويات التي لم تأخذ حالة بعد (للتشخيص)."""
        return self._inputs - set(self._assignments)


def reconcile_and_assert(
    competitor_source: Any,
    assignments: Any,
    *,
    context: str = "",
) -> LedgerReport:
    """
    يبني سجلاً، يُسجّل كل المنافسين، يُطبّق التخصيصات، ثم يقفل قفلاً كمومياً.

    ⚠️ هذه الدالة محرّك-محايدة (لا تعرف ``bootstrap`` ولا سكيمة العرض). محوّلُ
    الربط بين أقسام ``engine.py`` وهويات السجل سيُكتب لاحقاً في طبقة bootstrap
    (الخطوة 4) — هنا نبرهن متانة السجل فقط.

    Parameters
    ----------
    competitor_source : dict | list
        مصدر منتجات المنافسين (كما في ``register_inputs``).
    assignments : Mapping[RoutingID, state] | Iterable[tuple]
        خريطة كل هوية إلى حالتها الطرفية (تقبل LedgerState أو STATUS_*).
    context : str
        وسم تشخيصي يظهر في رسالة الخطأ.

    Returns
    -------
    LedgerReport
    """
    ledger = CompetitorLedger()
    ledger.register_inputs(competitor_source)
    ledger.assign_many(assignments)
    return ledger.assert_closed_loop(context=context)


# ══════════════════════════════════════════════════════════════════════════════
#  12. محوّل الهوية + كون المنافسين — ربط مخرجات engine.py بالسجل (v2.0)
#  engine.py محوره منتجاتنا ويُكدّس المنافسين في «جميع_المنافسين» (list[dict])
#  مخفياً هوياتهم. هنا نفكّكها ونعيد كل منافس إلى هويته في قاعدة الـ 129K.
#  أُثبت على بيانات حيّة: product_id يطابق DB.id بنسبة 100% للأقسام، واسم
#  المنافس يطابق DB.product_name بنسبة 100% للمفقودات.
# ══════════════════════════════════════════════════════════════════════════════

# أعمدة مخرجات المحرّك (عرض) — ثوابت محلية (ليست في conf.constants).
_COL_ALL_COMP   = "جميع_المنافسين"
_COL_MISS_NAME  = "منتج_المنافس"
_COL_MISS_CONF  = "مستوى_الثقة"
_SECTION_MATCH  = ("price_raise", "price_lower", "approved")
_SECTION_REVIEW = ("review",)


class CompetitorUniverse(NamedTuple):
    """كون منتجات المنافسين (مصدر الحقيقة) — مبني من قاعدة 129K."""

    all_ids:     frozenset                # كل المعرّفات (DB id كنصوص)
    name_to_ids: Dict[str, List[str]]     # اسم المنتج → معرّفاته (لِحلّ المفقودات)
    noise_ids:   frozenset                # معرّفات صُنّفت ضجيجاً/عيّنة

    def is_noise(self, pid: str) -> bool:
        return pid in self.noise_ids


def load_competitor_universe(db_path: str) -> CompetitorUniverse:
    """
    يحمّل كون المنافسين الكامل من قاعدة البيانات (خفيف — أعمدة المفاتيح فقط).

    يبني: مجموعة كل المعرّفات + خريطة اسم→معرّفات (لربط المفقودات) + مجموعة
    معرّفات الضجيج (عيّنات/أسعار صفرية) لتوجيهها إلى EXCLUDED_NOISE.
    """
    import sqlite3

    try:
        from config import REJECT_KEYWORDS as _reject
    except Exception:  # noqa: BLE001
        _reject = ["sample", "عينة", "عينه", "decant", "تقسيم", "split", "miniature"]
    reject = [str(w).lower() for w in _reject] + ["للتجربة", "للتجربه", "tester only"]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, product_name, price FROM competitor_products_store WHERE price > 0"
        ).fetchall()
    finally:
        conn.close()

    all_ids: set = set()
    name_to_ids: Dict[str, List[str]] = {}
    noise_ids: set = set()
    for _id, name, price in rows:
        pid = str(_id)
        all_ids.add(pid)
        nm = "" if name is None else str(name)
        name_to_ids.setdefault(nm, []).append(pid)
        low = nm.lower()
        try:
            cheap = price is not None and float(price) <= 1.0
        except (TypeError, ValueError):
            cheap = False
        if cheap or any(w in low for w in reject):
            noise_ids.add(pid)

    logger.info(
        "🌐 كون المنافسين: %d منتج | %d اسم فريد | %d ضجيج",
        len(all_ids), len(name_to_ids), len(noise_ids),
    )
    return CompetitorUniverse(
        all_ids=frozenset(all_ids),
        name_to_ids=name_to_ids,
        noise_ids=frozenset(noise_ids),
    )


def build_ledger_assignments(
    sections: Dict[str, "pd.DataFrame"],
    missing_df: Optional["pd.DataFrame"],
    universe: CompetitorUniverse,
    *,
    residual_state: LedgerState = LedgerState.AI_REVIEW_PENDING,
    sweep_residual: bool = True,
) -> Dict[str, LedgerState]:
    """
    يبني خريطة {معرّف منافس → حالة طرفية} من مخرجات التحليل (دالة نقية).

    أولوية الحسم (الأقوى يفوز): متطابق > مراجعة(قسم) > مفقود/مراجعة(مفقودات) >
    البقية. كل منافس في «جميع_المنافسين» لمنتج مملوك = متطابق (نملك المنتج).

    .. versionchanged:: v2.1
       ``residual_state`` الافتراضي أصبح ``AI_REVIEW_PENDING`` بدلاً من
       ``CONFIRMED_MISSING`` — المجهول ليس مفقوداً، بل مشبوه يحتاج مراجعة.

    Parameters
    ----------
    sections : dict[str, DataFrame]
        أقسام المحرّك (price_raise/price_lower/approved/review/excluded).
    missing_df : DataFrame | None
        المفقودات (تحمل اسم المنافس + مستوى الثقة).
    universe : CompetitorUniverse
        كون المنافسين الكامل.
    residual_state : LedgerState
        حالة البقية غير المصنّفة (افتراضياً AI_REVIEW_PENDING — المجهول ≠ مفقود).
    sweep_residual : bool
        إن ``False`` تُترك البقية بلا حالة — لِكشف الفجوة في القفل عمداً.

    Returns
    -------
    dict[str, LedgerState]
    """
    assign: Dict[str, LedgerState] = {}
    ids = universe.all_ids

    def _comps(value: Any):
        if value is None or isinstance(value, float):
            return
        try:
            for c in value:
                if isinstance(c, dict):
                    yield c
        except TypeError:
            return

    def _has_col(df: Any, col: str) -> bool:
        return df is not None and col in getattr(df, "columns", [])

    # 1) المتطابقون (نملك المنتج) — أعلى أولوية
    for skey in _SECTION_MATCH:
        df = sections.get(skey)
        if not _has_col(df, _COL_ALL_COMP):
            continue
        for lst in df[_COL_ALL_COMP]:
            for c in _comps(lst):
                pid = str(c.get("product_id", "")).strip()
                if pid in ids:
                    assign[pid] = LedgerState.CONFIRMED_MATCH

    # 2) مراجعة القسم — لا تتجاوز المتطابق
    for skey in _SECTION_REVIEW:
        df = sections.get(skey)
        if not _has_col(df, _COL_ALL_COMP):
            continue
        for lst in df[_COL_ALL_COMP]:
            for c in _comps(lst):
                pid = str(c.get("product_id", "")).strip()
                if pid in ids and pid not in assign:
                    assign[pid] = LedgerState.AI_REVIEW_PENDING

    # 3) المفقودات — اسم → معرّفات، لا تتجاوز ما سبق
    if _has_col(missing_df, _COL_MISS_NAME) and len(missing_df):
        names = missing_df[_COL_MISS_NAME].astype(str).tolist()
        if _COL_MISS_CONF in missing_df.columns:
            confs = missing_df[_COL_MISS_CONF].astype(str).tolist()
        else:
            confs = [""] * len(names)
        for nm, cf in zip(names, confs):
            state = (LedgerState.CONFIRMED_MISSING if cf == "green"
                     else LedgerState.AI_REVIEW_PENDING)
            for pid in universe.name_to_ids.get(nm, ()):
                if pid not in assign:
                    assign[pid] = state

    # 4) البقية (لم يلمسها التحليل أصلاً = التسرّب التاريخي) — كنس مُصنَّف
    if sweep_residual:
        for pid in ids:
            if pid not in assign:
                assign[pid] = (LedgerState.EXCLUDED_NOISE
                               if universe.is_noise(pid) else residual_state)
    return assign


def reconcile_competitor_universe(
    universe: CompetitorUniverse,
    sections: Dict[str, "pd.DataFrame"],
    missing_df: Optional["pd.DataFrame"],
    *,
    context: str = "",
    sweep_residual: bool = True,
) -> LedgerReport:
    """
    الإقفال الكوني: يُسجّل كل كون المنافسين، يُسند كل معرّف عبر المحوّل، ثم
    يقفل قفلاً كمومياً. أي تسرّب ⇒ RuntimeError يذكر المعرّف بالضبط.
    """
    assignments = build_ledger_assignments(
        sections, missing_df, universe, sweep_residual=sweep_residual
    )
    ledger = CompetitorLedger()
    ledger.register_all(universe.all_ids)
    ledger.assign_many(assignments)
    return ledger.assert_closed_loop(context=context)
