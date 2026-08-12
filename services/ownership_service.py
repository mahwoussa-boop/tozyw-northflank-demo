"""services/ownership_service.py — طبقة الملكية الموحّدة (جديد/نفذت ↔ كتالوجنا).

تُثري صفوف منتجات المنافسين بقرار «هل نملكه؟» عبر ``MatchingService`` نفسها التي
تستخدمها المفقودات ([missing_service.py]) — فتُمنع التكرارات بالتصميم:

  • OWNED   ⇒ نملكه (لا إنشاء؛ في «نفذت» نعرض بطاقة مقارنة + تصفير الكمية).
  • REVIEW  ⇒ محتمل لدينا (تحقّق بشري).
  • MISSING ⇒ ليس لدينا (مرشّح للإضافة).

النواة (``enrich_ownership``) **خالصة وقابلة للاختبار بلا قاعدة بيانات**؛ التحميل من
جدول ``our_catalog`` معزول في دوال رفيعة في الأسفل.
"""
from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping

from services.matching_service import MatchingService, Ownership, miss_bare

# مفاتيح الإثراء المُضافة لكل صف (مصدر حقيقة واحد يمنع الأخطاء الإملائية).
KEY_OWNERSHIP = "ownership"            # "owned" | "review" | "missing"
KEY_OUR_MATCH = "our_match"            # اسم منتجنا المطابق (أو None)
KEY_OUR_PRODUCT_ID = "our_product_id"  # معرّف سلة لمنتجنا (أو "")
KEY_MATCH_SCORE = "match_score"        # درجة التشابه 0..100
KEY_MATCH_REASON = "match_reason"      # سبب المراجعة (إن وُجد)

_NAME_KEYS = ("product_name", "المنتج", "منتج_المنافس", "name")
_BRAND_KEYS = ("brand", "الماركة", "brand_name")


def _first(row: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    """أول قيمة نصّية صالحة من بين مفاتيح بديلة. خالص."""
    for k in keys:
        v = row.get(k) if hasattr(row, "get") else None
        if v is not None:
            s = str(v).strip()
            if s and s.lower() not in ("nan", "none"):
                return s
    return ""


def enrich_row_ownership(
    row: Mapping[str, Any],
    matching: MatchingService,
    name_to_id: Mapping[str, str],
) -> dict[str, Any]:
    """نسخة من الصف مُثراة بقرار الملكية + معرّف منتجنا. خالصة (لا تُعدّل الأصل)."""
    enriched = dict(row)
    name = _first(row, _NAME_KEYS)
    if not name:
        enriched[KEY_OWNERSHIP] = Ownership.MISSING.value
        enriched[KEY_OUR_MATCH] = None
        enriched[KEY_OUR_PRODUCT_ID] = ""
        enriched[KEY_MATCH_SCORE] = 0.0
        enriched[KEY_MATCH_REASON] = ""
        return enriched
    outcome = matching.evaluate(name, _first(row, _BRAND_KEYS))
    our_match = outcome.our_match
    enriched[KEY_OWNERSHIP] = outcome.ownership.value
    enriched[KEY_OUR_MATCH] = our_match
    enriched[KEY_OUR_PRODUCT_ID] = str(name_to_id.get(our_match, "")) if our_match else ""
    enriched[KEY_MATCH_SCORE] = float(outcome.score)
    enriched[KEY_MATCH_REASON] = outcome.reason
    return enriched


def enrich_ownership(
    rows: Iterable[Mapping[str, Any]],
    matching: MatchingService,
    name_to_id: Mapping[str, str],
) -> list[dict[str, Any]]:
    """يُثري قائمة صفوف منافسين بقرار الملكية. خالصة."""
    return [enrich_row_ownership(r, matching, name_to_id) for r in rows]


def summarize_ownership(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """عدّاد owned/review/missing لصفوف مُثراة (لعدّادات الواجهة). خالص."""
    counts = {
        Ownership.OWNED.value: 0,
        Ownership.REVIEW.value: 0,
        Ownership.MISSING.value: 0,
    }
    for r in rows:
        o = str(r.get(KEY_OWNERSHIP, Ownership.MISSING.value)) if hasattr(r, "get") else ""
        if o in counts:
            counts[o] += 1
    return counts


# ── تجميع منتجات المنافسين حسب المنتج (متعدد المنافسين) ────────────────────
def _num(value: Any) -> float:
    try:
        return float(value if value is not None else 0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_competitor_products(
    rows: Iterable[Mapping[str, Any]], matching: MatchingService,
) -> list[dict[str, Any]]:
    """يجمّع صفوف منتجات المنافسين حسب الاسم المُطبَّع → صف واحد لكل منتج.

    يدمج المتاجر (``المنافسون``/``عدد_المنافسين``)، ويأخذ **أرخص سعر** وأول صورة/
    ماركة/رابط صالح. مخطّط المخرجات يطابق بطاقة المفقودات (مفاتيح عربية) فتُعاد
    استخدامها. خالص قابل للاختبار. يحافظ على ترتيب أول ظهور.
    """
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        name = str(r.get("product_name") or r.get("منتج_المنافس") or "").strip()
        if not name:
            continue
        key = miss_bare(name, matching.kernel) or name.lower()
        price = _num(r.get("price") if r.get("price") is not None else r.get("سعر_المنافس"))
        comp = str(r.get("competitor") or r.get("المنافس") or "").strip()
        g = groups.get(key)
        if g is None:
            g = {
                "منتج_المنافس": name,
                "سعر_المنافس": price,
                "صورة_المنافس": str(r.get("image_url") or ""),
                "الماركة": str(r.get("brand") or ""),
                "رابط_المنافس": str(r.get("product_url") or ""),
                "first_seen_at": r.get("first_seen_at"),
                "_stores": [],
            }
            groups[key] = g
            order.append(key)
        if price > 0 and (g["سعر_المنافس"] <= 0 or price < g["سعر_المنافس"]):
            g["سعر_المنافس"] = price
        if not g["صورة_المنافس"] and r.get("image_url"):
            g["صورة_المنافس"] = str(r.get("image_url"))
        if not g["الماركة"] and r.get("brand"):
            g["الماركة"] = str(r.get("brand"))
        if not g["رابط_المنافس"] and r.get("product_url"):
            g["رابط_المنافس"] = str(r.get("product_url"))
        if comp and comp not in g["_stores"]:
            g["_stores"].append(comp)

    out: list[dict[str, Any]] = []
    for key in order:
        g = groups[key]
        stores = g.pop("_stores")
        g["المنافسون"] = ",".join(stores)   # فاصلة ASCII = مسار البيانات السليمة في _restore_stores
        g["المنافس"] = stores[0] if stores else ""
        g["عدد_المنافسين"] = len(stores)
        out.append(g)
    return out


def prepare_competitor_feed(
    rows: Iterable[Mapping[str, Any]],
    matching: MatchingService,
    name_to_id: Mapping[str, str],
) -> list[dict[str, Any]]:
    """يجمّع صفوف المنافسين ثم يُثريها بقرار الملكية — جاهزة للعرض. خالص."""
    return enrich_ownership(
        aggregate_competitor_products(rows, matching), matching, name_to_id,
    )


# ── تحميل كتالوجنا (معزول عن النواة الخالصة) ──────────────────────────────
def load_our_catalog_index() -> tuple[list[str], dict[str, str]]:
    """يقرأ ``our_catalog`` → (أسماء منتجاتنا، خريطة اسم→معرّف). رفيع (يلمس DB)."""
    from utils.db_manager import get_our_catalog

    names: list[str] = []
    name_to_id: dict[str, str] = {}
    for r in get_our_catalog():
        nm = str(r.get("product_name") or "").strip()
        if not nm:
            continue
        names.append(nm)
        pid = str(r.get("product_id") or "").strip()
        if pid:
            name_to_id[nm] = pid
    return names, name_to_id


def build_ownership_matcher() -> tuple[MatchingService, dict[str, str]]:
    """يبني ``MatchingService`` من كتالوجنا + خريطة اسم→معرّف. رفيع."""
    names, name_to_id = load_our_catalog_index()
    return MatchingService(names), name_to_id


# ── مذكّرة على مستوى العملية (لأجل التسخين المسبق) ────────────────────────────
# لماذا هنا ولا تكفي ``st.cache_resource`` في الصفحة: كاش Streamlit لا يُملأ إلا
# من داخل تشغيلة سكربت (جلسة متصفّح)، فأول فتح بعد إعادة تشغيل الخادم يدفع
# **5,941م.ث** مقيسة (2026-07-25) لبناء المُطابِق. المذكّرة هنا يملؤها خيط
# التسخين في ``services/warmup.py`` عند الإقلاع، فتجده الصفحة جاهزاً.
# المفتاح هو بصمة الكتالوج نفسها التي تستعملها الصفحة ⇒ كتالوج تغيّر = بناء جديد.
_MATCHER_MEMO: dict[str, tuple[MatchingService, dict[str, str]]] = {}
_MATCHER_MEMO_MAX = 2          # كما ``max_entries=2`` في الصفحة — لا تراكم
_MATCHER_LOCK = threading.Lock()


def build_ownership_matcher_cached(
    signature: str,
) -> tuple[MatchingService, dict[str, str]]:
    """يعيد المُطابِق من المذكّرة إن طابقت البصمة، وإلا يبنيه **مرّة واحدة**.

    **لماذا القفل يُمسَك أثناء البناء لا بعده:** المتسابقان الحقيقيان هما خيط
    التسخين وأول جلسة متصفّح تفتح الصفحة قبل اكتماله. لو بنى كلٌّ نسخته ثم
    تصالحا على واحدة، لدفع الجهاز ضعف الحساب وضعف الذاكرة لحظياً — وهو جهاز
    بـ7.8غ.ب يشغّل متجراً حيّاً. الانتظار هنا ليس خسارة: المنتظِر كان سيبني
    نفس الشيء بنفسه على أي حال، فهو ينتظر عملاً لا بدّ منه.

    فحص مزدوج (قبل القفل وبعده) حتى لا تدفع الإصاباتُ الشائعة ثمنَ التزاحم.
    """
    hit = _MATCHER_MEMO.get(signature)
    if hit is not None:
        return hit
    with _MATCHER_LOCK:
        hit = _MATCHER_MEMO.get(signature)
        if hit is not None:             # سبقنا خيطٌ آخر أثناء انتظارنا
            return hit
        built = build_ownership_matcher()
        while len(_MATCHER_MEMO) >= _MATCHER_MEMO_MAX:
            _MATCHER_MEMO.pop(next(iter(_MATCHER_MEMO)))
        _MATCHER_MEMO[signature] = built
    return built
