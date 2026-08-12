"""
utils/product_key.py
Stable identity key for any product across stores and sources.
Used as the single source of truth to prevent duplicates from the root.
"""
import hashlib
import re
import unicodedata
from urllib.parse import urlparse, urlunparse

_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
_NON_ALNUM = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)
_MULTI_SPACE = re.compile(r"\s+")

_ARABIC_NORMALIZE = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
})


def normalize_text(s) -> str:
    if not s:
        return ""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFKC", s)
    s = _ARABIC_DIACRITICS.sub("", s)
    s = s.translate(_ARABIC_NORMALIZE)
    s = _NON_ALNUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return s


def normalize_url(u) -> str:
    if not u:
        return ""
    try:
        p = urlparse(str(u).strip().lower())
        netloc = p.netloc.replace("www.", "")
        path = p.path.rstrip("/")
        return urlunparse((p.scheme or "https", netloc, path, "", "", ""))
    except Exception:
        return str(u).strip().lower()


def make_product_key(name="", store="", url="") -> str:
    """Deterministic SHA1 key — same product → same key, always.

    ملاحظة: هذا مفتاح *مصدري* (مرتبط بالمتجر/الرابط) — صالح لمنع تكرار نفس
    الصفّ من نفس المصدر، لكنه ليس بصمة هوية عبر المتاجر. لمنع التكرار في
    كتالوجنا (SKU المنافس ≠ SKU متجرك) استخدم ``fingerprint`` أدناه.
    """
    n = normalize_text(name)
    s = normalize_text(store)
    u = normalize_url(url)
    raw = f"{n}|{s}|{u}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════════════════════
#  بصمة هوية المنتج عبر المتاجر (Cross-store fingerprint)
# ════════════════════════════════════════════════════════════════════════════
#  المشكلة: SKU المنافس ≠ SKU متجرك، و md5(brand|full_name) هشّ (تطبيع ضعيف:
#  «سوفاج EDP 100مل» ≠ «سوفاج او دو بارفيوم 100 مل»). الحل: بصمة على *هوية*
#  المنتج = ماركة + نواة الاسم (نفس سلسلة المطابقة miss_bare) + الحجم + التركيز.
#  المفاتيح القاطعة (barcode/sku) تُستخدم للحسم حين تتوفّر.
#
#  ⚙️ إعادة استخدام لا ازدواج: نواة الاسم/الماركة من ``services.matching_service``
#  (miss_bare + kernel) كي تتطابق طبقة «البصمة التامة» مع حارس التشابه حرفياً،
#  والتركيز من ``engines.engine.extract_type``. عند تعذّر تحميل المحرّك (بيئة
#  مجرّدة) نتدرّج إلى ``normalize_text`` المحلي فيبقى الملف صالحاً للاستيراد.

import re as _re
from dataclasses import dataclass

_SIZE_FP_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*(?:ml|مل|ملي|milliliter)?", _re.I)

# خريطة تركيز محلية احتياطية (حين يغيب المحرّك فقط).
_CONC_FALLBACK = {
    "edp": "edp", "eau de parfum": "edp", "بارفيوم": "edp", "برفيوم": "edp",
    "بارفان": "edp", "parfum": "edp",
    "edt": "edt", "eau de toilette": "edt", "تواليت": "edt", "توالت": "edt",
    "edc": "edc", "cologne": "edc", "كولون": "edc", "كولونيا": "edc",
    "elixir": "elixir", "اكسير": "elixir",
    "extrait": "parfum", "اكستريت": "parfum",
}

_KERNEL_CACHE = []  # [kernel] أو [None] — كاش كسول لتفادي إعادة المحاولة


def _kernel():
    """نواة المطابعات من matching_service (كسولة، مع كاش). None عند التعذّر."""
    if _KERNEL_CACHE:
        return _KERNEL_CACHE[0]
    try:
        from services.matching_service import load_engine_kernel
        k = load_engine_kernel()
    except Exception:
        k = None
    _KERNEL_CACHE.append(k)
    return k


def _name_core(name: str, brand: str = "") -> str:
    """نواة الاسم للمطابقة — نفس سلسلة ``miss_bare`` (نقطة الالتقاء مع الحارس).

    تتدرّج إلى ``normalize_text`` حين يغيب المحرّك كي لا ينكسر الاستيراد.
    """
    k = _kernel()
    if k is not None:
        try:
            from services.matching_service import miss_bare
            core = miss_bare(name, k)
            if core:
                return core
        except Exception:
            pass
    # تدرّج محلي: تطبيع + إسقاط أرقام الحجم.
    base = normalize_text(name)
    base = _re.sub(r"\b\d+\s*(?:ml|مل|ملي)?\b", " ", base)
    return _re.sub(r"\s+", " ", base).strip()


def _brand_norm(name: str, brand: str = "") -> str:
    """ماركة مُطبَّعة — متّسقة مع حارس الماركة في MatchingService."""
    k = _kernel()
    if k is not None:
        try:
            b = k.extract_brand_fast(name) or k.extract_brand_fast(brand) or brand
            return k.normalize(b or "")
        except Exception:
            pass
    return normalize_text(brand)


def _conc_norm(name: str, concentration: str = "") -> str:
    """رمز التركيز الموحّد (edp/edt/…)، يميّز Intense كمنتج مختلف."""
    k = _kernel()
    # المصدر: حقل التركيز الصريح إن وُجد، وإلا اسم المنتج.
    source = concentration or name
    if k is not None:
        try:
            from engines.engine import extract_type
            code = extract_type(source)
            if code:
                return code.lower().replace(" ", "")
        except Exception:
            pass
    low = str(source or "").lower()
    for needle, code in _CONC_FALLBACK.items():
        if needle in low:
            return code
    return ""


def _size_norm(name: str, size=None) -> int:
    """الحجم بالمل كعدد صحيح (0 إن لم يُعرف)."""
    if size is not None and str(size).strip() != "":
        m = _SIZE_FP_RE.search(str(size))
        if m:
            try:
                return int(round(float(m.group(1))))
            except (ValueError, TypeError):
                pass
    k = _kernel()
    if k is not None:
        try:
            ml = k.extract_size(name)
            if ml and ml > 0:
                return int(round(ml))
        except Exception:
            pass
    m = _SIZE_FP_RE.search(str(name or ""))
    if m:
        try:
            v = int(round(float(m.group(1))))
            return v if 1 <= v <= 9999 else 0
        except (ValueError, TypeError):
            pass
    return 0


def _barcode_norm(barcode) -> str:
    """باركود/GTIN قاطع — أرقام فقط، ويُهمَل ما يقصر عن 6 خانات."""
    digits = _re.sub(r"\D", "", str(barcode or ""))
    return digits if len(digits) >= 6 else ""


def _sku_norm(sku) -> str:
    """SKU قاطع — توحيد حالة الأحرف وإسقاط المسافات/الرموز."""
    s = _re.sub(r"\s+", "", str(sku or "")).strip().upper()
    return s if s and s not in ("NAN", "NONE") else ""


@dataclass(frozen=True)
class ProductFingerprint:
    """بصمة هوية منتج عبر المتاجر + مفاتيح قاطعة (barcode/sku)."""

    brand: str
    core: str
    size_ml: int
    concentration: str
    barcode: str = ""
    sku: str = ""

    @property
    def key(self) -> str:
        """السلسلة القانونية: ``brand|core|<size>ml|conc`` (مطابقة لعقد الحمولة)."""
        return f"{self.brand}|{self.core}|{self.size_ml}ml|{self.concentration}"

    @property
    def is_identifiable(self) -> bool:
        """صالحة للحسم؟ نحتاج على الأقل نواة اسم غير فارغة (أو مفتاح قاطع)."""
        return bool(self.core) or bool(self.barcode) or bool(self.sku)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "brand": self.brand, "core": self.core,
            "size_ml": self.size_ml, "concentration": self.concentration,
            "barcode": self.barcode, "sku": self.sku,
        }


def fingerprint(
    name: str,
    brand: str = "",
    size=None,
    concentration: str = "",
    barcode="",
    sku="",
) -> ProductFingerprint:
    """يبني بصمة هوية المنتج من حقول خام (أيٌّ منها قد يكون فارغاً).

    - ``name``: اسم المنتج (يُشتقّ منه الباقي عند غياب الحقول الصريحة).
    - ``brand``/``size``/``concentration``: تُفضَّل على المشتقّ من الاسم إن وُجدت.
    - ``barcode``/``sku``: مفاتيح قاطعة (تطابق = تكرار مؤكّد) حين تتوفّر.
    """
    return ProductFingerprint(
        brand=_brand_norm(name, brand),
        core=_name_core(name, brand),
        size_ml=_size_norm(name, size),
        concentration=_conc_norm(name, concentration),
        barcode=_barcode_norm(barcode),
        sku=_sku_norm(sku),
    )


def fingerprint_key(name: str, brand: str = "", size=None, concentration: str = "") -> str:
    """اختصار: السلسلة القانونية للبصمة فقط (للتتبّع/التسجيل في الحمولة)."""
    return fingerprint(name, brand, size, concentration).key
