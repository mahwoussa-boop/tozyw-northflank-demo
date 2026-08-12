"""
engines/duplicate_detector.py
3-layer duplicate detection:
  1. Exact key match (instant, deterministic)
  2. Fuzzy similarity via RapidFuzz (>=95 → duplicate, 85-94 → review)
  3. Semantic AI verification (only for borderline cases)

Returns a structured Verdict so the smart router can act without ambiguity.
"""
from dataclasses import dataclass
from typing import Iterable, Optional, Callable

from utils.product_key import make_product_key, normalize_text

try:
    from rapidfuzz import fuzz, process
    _HAS_FUZZ = True
except Exception:
    _HAS_FUZZ = False


DUPLICATE_THRESHOLD = 95.0   # >= → DUPLICATE
REVIEW_THRESHOLD = 85.0      # 85-94 → REVIEW
MATCH_THRESHOLD = 70.0       # 70-84 → possibly matched, ask AI
SEMANTIC_THRESHOLD = 60.0    # < 60 → NEW (genuinely missing)


@dataclass
class Verdict:
    decision: str            # DUPLICATE | REVIEW | MATCHED | NEW
    confidence: float        # 0..100
    duplicate_of: Optional[str] = None   # product_key of original
    matched_with: Optional[str] = None   # name of catalog item
    layer: str = "exact"     # exact | fuzzy | semantic
    reason: str = ""

    def to_dict(self):
        return {
            "decision": self.decision, "confidence": self.confidence,
            "duplicate_of": self.duplicate_of, "matched_with": self.matched_with,
            "layer": self.layer, "reason": self.reason,
        }


def _fuzzy_best(name: str, candidates: list) -> tuple:
    """Returns (best_name, score, idx) using RapidFuzz, or (None, 0, -1)."""
    if not _HAS_FUZZ or not candidates:
        return (None, 0.0, -1)
    norm_name = normalize_text(name)
    norm_pool = [normalize_text(c.get("name", "")) for c in candidates]
    best = process.extractOne(norm_name, norm_pool, scorer=fuzz.token_set_ratio)
    if not best:
        return (None, 0.0, -1)
    matched_str, score, idx = best
    return (candidates[idx].get("name", ""), float(score), idx)


def detect(product: dict,
           catalog: list,
           existing_keys: Optional[set] = None,
           ai_verify: Optional[Callable[[str, str], bool]] = None) -> Verdict:
    """
    product:        {name, store, url, ...}
    catalog:        [{name, key?, ...}, ...] — our products / known items
    existing_keys:  set of product_keys already stored (Layer 1)
    ai_verify:      optional callable(name_a, name_b) -> bool, for Layer 3
    """
    name = product.get("name", "") or product.get("product_name", "")
    store = product.get("store", "") or product.get("competitor", "")
    url = product.get("url", "") or product.get("link", "")
    key = make_product_key(name, store, url)

    # Layer 1: exact key duplicate
    if existing_keys and key in existing_keys:
        return Verdict("DUPLICATE", 100.0, duplicate_of=key,
                       layer="exact", reason="نفس مفتاح المنتج موجود مسبقاً")

    # Layer 2: fuzzy match against catalog
    matched_name, score, idx = _fuzzy_best(name, catalog or [])

    if score >= DUPLICATE_THRESHOLD:
        dup_key = (catalog[idx].get("key")
                   or make_product_key(matched_name,
                                       catalog[idx].get("store", ""),
                                       catalog[idx].get("url", "")))
        return Verdict("DUPLICATE", score, duplicate_of=dup_key,
                       matched_with=matched_name, layer="fuzzy",
                       reason=f"تشابه نصي {score:.1f}%")

    if score >= REVIEW_THRESHOLD:
        return Verdict("REVIEW", score, matched_with=matched_name, layer="fuzzy",
                       reason=f"تشابه متوسط {score:.1f}% — يحتاج تحقق")

    # Layer 3: semantic AI verify for borderline (70-84)
    if score >= MATCH_THRESHOLD and ai_verify and matched_name:
        try:
            same = bool(ai_verify(name, matched_name))
            if same:
                dup_key = (catalog[idx].get("key")
                           or make_product_key(matched_name,
                                               catalog[idx].get("store", ""),
                                               catalog[idx].get("url", "")))
                return Verdict("DUPLICATE", max(score, 90.0),
                               duplicate_of=dup_key, matched_with=matched_name,
                               layer="semantic", reason="AI أكّد أنه نفس المنتج")
            return Verdict("REVIEW", score, matched_with=matched_name,
                           layer="semantic", reason="AI غير متأكد")
        except Exception as e:
            return Verdict("REVIEW", score, matched_with=matched_name,
                           layer="semantic", reason=f"خطأ AI: {e}")

    if score >= MATCH_THRESHOLD:
        return Verdict("REVIEW", score, matched_with=matched_name, layer="fuzzy",
                       reason=f"تشابه ضعيف {score:.1f}% — يحتاج مراجعة")

    return Verdict("NEW", score, layer="fuzzy",
                   reason="منتج جديد — لا يوجد تشابه كافٍ")


# ════════════════════════════════════════════════════════════════════════════
#  منع التكرار بالبصمة — is_duplicate (عقد: match | likely | new)
# ════════════════════════════════════════════════════════════════════════════
#  يُستدعى في مسار «المفقودات» *قبل* بناء الحمولة. منطق القرار (بالأولوية):
#    باركود/GTIN مطابق ⇒ match · sku مطابق ⇒ match · بصمة تامة مطابقة ⇒ match
#    · تشابه ماركي عالٍ (حارس MatchingService: ماركة+حجم+جنس) ⇒ match/likely
#    · وإلا ⇒ new (جديد فعلاً، يُرسَل).
#
#  ⚙️ إعادة استخدام: البصمة من ``utils.product_key.fingerprint``، والطبقة
#  الضبابية من ``services.matching_service.MatchingService`` (الحارس نفسه
#  المستخدَم حيّاً في كشف المفقودات) — لا منطق مطابقة مكرّر.
from utils.product_key import fingerprint as _fingerprint

MATCH = "match"      # تكرار مؤكّد — لا يُرسَل
LIKELY = "likely"    # تكرار محتمل — مراجعة بشرية
NEW = "new"          # جديد فعلاً — يُرسَل


@dataclass
class DupResult:
    decision: str                 # match | likely | new
    reason: str = ""
    matched: Optional[str] = None  # مرجع/اسم العنصر المطابق في الكتالوج
    score: float = 0.0
    layer: str = ""               # barcode | sku | fingerprint | fuzzy | none
    fingerprint: str = ""

    def to_dict(self):
        return {
            "decision": self.decision, "reason": self.reason,
            "matched": self.matched, "score": self.score,
            "layer": self.layer, "fingerprint": self.fingerprint,
        }


# مفاتيح الحقول المقبولة في قاموس المرشّح (يطابق عقد الحمولة + مخرجات الكاشط).
_NAME_KEYS = ("name", "أسم المنتج", "اسم المنتج", "المنتج", "منتج_المنافس", "product_name")
_BRAND_KEYS = ("brand", "الماركة", "brand_name")
_SIZE_KEYS = ("size", "الحجم", "size_ml")
_CONC_KEYS = ("concentration", "التركيز", "conc")
_BARCODE_KEYS = ("barcode", "الباركود", "GTIN", "gtin")
_SKU_KEYS = ("sku", "رمز المنتج sku", "SKU")


def _pick(d: dict, keys) -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip() not in ("", "nan", "None"):
            return str(v).strip()
    return ""


class CatalogIndex:
    """فهرس بصمات كتالوجنا — يُبنى مرّة ويُستعلَم لكل مرشّح.

    يجمع ثلاث طبقات حسم سريعة (باركود/sku/بصمة تامة) + طبقة تشابه ماركي
    عبر ``MatchingService`` (تُبنى كسولاً عند أول حاجة). ``ref`` هو ما
    يُعاد عند المطابقة (اسم/معرّف المنتج المخزَّن).
    """

    def __init__(self) -> None:
        self._fps: dict = {}        # fingerprint.key -> ref
        self._barcodes: dict = {}   # barcode -> ref
        self._skus: dict = {}       # sku -> ref
        self._names: list = []      # أسماء خام لبناء الحارس الضبابي
        self._matcher = None
        self._matcher_built = False

    def add(self, name, brand="", size=None, concentration="",
            barcode="", sku="", ref=None) -> None:
        fp = _fingerprint(name, brand, size, concentration, barcode, sku)
        ref = ref if ref is not None else name
        if fp.barcode:
            self._barcodes.setdefault(fp.barcode, ref)
        if fp.sku:
            self._skus.setdefault(fp.sku, ref)
        if fp.core:
            self._fps.setdefault(fp.key, ref)
            self._names.append(str(name))
        self._matcher_built = False  # أُبطِل الحارس — سيُعاد بناؤه عند الحاجة

    def register(self, name, brand="", size=None, concentration="",
                 barcode="", sku="", ref=None) -> None:
        """يسجّل بصمة في طبقات الحسم القاطعة فقط (دون لمس الحارس الضبابي).

        للاستخدام في dedup *داخل الدفعة*: نلتقط المكرر التام/القاطع للعناصر
        المقبولة حديثاً بلا إعادة بناء MatchingService (تفادي O(n²)).
        """
        fp = _fingerprint(name, brand, size, concentration, barcode, sku)
        ref = ref if ref is not None else name
        if fp.barcode:
            self._barcodes.setdefault(fp.barcode, ref)
        if fp.sku:
            self._skus.setdefault(fp.sku, ref)
        if fp.core:
            self._fps.setdefault(fp.key, ref)

    @classmethod
    def build(cls, records, *, ref_key=None) -> "CatalogIndex":
        """يبني الفهرس من تكرارات (dicts). ``ref_key`` = عمود المرجع المُعاد."""
        idx = cls()
        for r in records or []:
            if not isinstance(r, dict):
                idx.add(str(r))
                continue
            ref = r.get(ref_key) if ref_key else _pick(r, ("product_id", "id")) or None
            idx.add(
                name=_pick(r, _NAME_KEYS), brand=_pick(r, _BRAND_KEYS),
                size=_pick(r, _SIZE_KEYS) or None, concentration=_pick(r, _CONC_KEYS),
                barcode=_pick(r, _BARCODE_KEYS), sku=_pick(r, _SKU_KEYS), ref=ref,
            )
        return idx

    def __len__(self) -> int:
        return len(self._fps)

    def _matcher_for(self):
        """يبني/يعيد حارس MatchingService كسولاً. None عند التعذّر أو فراغ."""
        if self._matcher_built:
            return self._matcher
        self._matcher_built = True
        self._matcher = None
        if self._names:
            try:
                from services.matching_service import MatchingService
                self._matcher = MatchingService(self._names)
            except Exception:
                self._matcher = None
        return self._matcher


def is_duplicate(candidate, index: CatalogIndex, *, use_fuzzy: bool = True) -> DupResult:
    """يقرّر إن كان ``candidate`` موجوداً في الكتالوج: match | likely | new.

    ``candidate``: dict (مفاتيح عربية/إنجليزية مدعومة) أو اسم نصّي.
    ``index``: ``CatalogIndex`` مبنيّ من كتالوجنا + المنتجات المُرسَلة سابقاً.
    """
    if isinstance(candidate, str):
        candidate = {"name": candidate}
    name = _pick(candidate, _NAME_KEYS)
    brand = _pick(candidate, _BRAND_KEYS)
    fp = _fingerprint(
        name, brand,
        _pick(candidate, _SIZE_KEYS) or None, _pick(candidate, _CONC_KEYS),
        _pick(candidate, _BARCODE_KEYS), _pick(candidate, _SKU_KEYS),
    )

    # ── طبقات الحسم القاطعة ──
    if fp.barcode and fp.barcode in index._barcodes:
        return DupResult(MATCH, "باركود مطابق", index._barcodes[fp.barcode],
                         100.0, "barcode", fp.key)
    if fp.sku and fp.sku in index._skus:
        return DupResult(MATCH, "SKU مطابق", index._skus[fp.sku],
                         100.0, "sku", fp.key)
    if fp.core and fp.key in index._fps:
        return DupResult(MATCH, "بصمة مطابقة", index._fps[fp.key],
                         100.0, "fingerprint", fp.key)

    # ── طبقة التشابه الماركي (حارس ماركة+حجم+جنس) ──
    if use_fuzzy and name:
        matcher = index._matcher_for()
        if matcher is not None:
            try:
                from services.matching_service import Ownership
                outcome = matcher.evaluate(name, brand)
                if outcome.ownership == Ownership.OWNED:
                    return DupResult(MATCH, outcome.reason or "تطابق ماركي مؤكّد",
                                     outcome.our_match, outcome.score, "fuzzy", fp.key)
                if outcome.ownership == Ownership.REVIEW:
                    return DupResult(LIKELY, outcome.reason or "تشابه عالٍ — مراجعة",
                                     outcome.our_match, outcome.score, "fuzzy", fp.key)
            except Exception:
                pass

    return DupResult(NEW, "منتج جديد — لا يطابق الكتالوج", None, 0.0, "none", fp.key)
