"""ui/components/product_card.py — محلّلات بطاقة المقارنة (نقية، بلا عرض).

تفهم **مسارَي البيانات** وتوحّدهما:
- مسار التسعير: صورة منتجنا في ``صورة_منتجنا`` وقائمة المنافسين في ``جميع_المنافسين``
  (مفاتيح إنجليزية: name/price/competitor/image_url/product_url/size/score).
- مسار المفقودات: التفاصيل في ``تفاصيل_المنافسين`` (مفاتيح عربية).
``parse_competitor_details`` توحّدهما في قائمة قواميس مطبّعة، و``build_card`` يبني
``CardView``. يستهلكها العرض الجديد في ``comparison_card`` (الحَلَبة/المفقودات).
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from conf.constants import (
    COL_BRAND,
    COL_COMP_DETAILS,
    COL_COMP_IMAGE,
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_COMP_STORE,
    COL_DIFF,
    COL_MATCH_RATIO,
    COL_OUR_ID,
    COL_OUR_NAME,
    COL_OUR_PRICE,
    COL_SIZE,
    COL_TYPE,
)
# مستخرجا روابط المنتج القويّان (يعالجان كل أسماء الأعمدة + جميع_المنافسين). utils خالص (بلا streamlit).
from utils.data_helpers import (
    competitor_product_url_from_row,
    our_product_url_from_row,
)

# أعمدة عرض من مخرجات محرك التسعير (ليست في conf.constants — أعمدة عرض داخلية).
# #PRESERVED_LOGIC: engines.engine.run_full_analysis + app.py:2737 (صورة_منتجنا/جميع_المنافسين).
_COL_OUR_IMAGE = "صورة_منتجنا"   # قائمة روابط مفصولة بفواصل، الأول هو الرئيسي
_COL_ALL_COMP = "جميع_المنافسين"  # list[dict] بمفاتيح إنجليزية (مسار التسعير)

# أدوات «⋯» على البطاقة — روابط بحث أصلية (Markdown، بلا HTML).
_MARKET_SEARCH = "https://www.google.com/search?q="
_OUR_DOMAIN = "mahwous.com"

# خريطتا مفاتيح قاموس المنافس الواحد (المفقودات=عربية، التسعير=إنجليزية).
_KEYS_AR = {"store": "المنافس", "name": "اسم_المنتج", "price": "السعر",
            "image": "الصورة", "link": "الرابط", "size": "الحجم", "score": "score"}
_KEYS_EN = {"store": "competitor", "name": "name", "price": "price",
            "image": "image_url", "link": "product_url", "size": "size", "score": "score"}


def _get(row: Any, key: str, default: Any = "") -> Any:
    """قراءة آمنة من صف/قاموس (تُرجع الافتراضي عند None/NaN/'nan')."""
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    value = getter(key, default)
    return default if value is None or str(value) == "nan" else value


def _to_float(value: Any) -> float:
    """تحويل آمن إلى عدد عشري (0.0 عند الفشل)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_url(value: Any) -> str:
    """أول رابط http من سلسلة روابط مفصولة بفواصل (صورة_منتجنا قد تحمل عدّة)."""
    for part in str(value or "").split(","):
        part = part.strip()
        if part.lower().startswith("http"):
            return part
    return ""


@dataclass(frozen=True)
class CardView:
    """حقول البطاقة الجاهزة للعرض."""

    our_name: str
    our_price: float
    our_sku: str
    our_brand: str
    our_size: str
    our_type: str
    our_image: str
    our_link: str
    comp_name: str
    comp_price: float
    comp_store: str
    comp_image: str
    comp_link: str
    diff: float
    match_pct: float
    comp_images: list = field(default_factory=list)  # ج3 خ2: معرض المنافس (عرض فقط)


def _coerce_list(raw: Any) -> list:
    """يحوّل قيمة عمود إلى قائمة بأمان (None/NaN/''/JSON-str/list/ndarray)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    if hasattr(raw, "tolist") and not isinstance(raw, (str, list)):
        raw = raw.tolist()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    return raw if isinstance(raw, list) else []


def _norm_detail(item: dict, keys: dict[str, str]) -> dict[str, Any]:
    """يطبّع قاموس منافس واحد إلى مخطط موحّد (store/name/price/image/link/size).

    ``images`` (ج3 خ2، عرض فقط): قائمة معرض المنافس — موجودة فقط في مسار
    التسعير (``comp_images`` من ``CompIndex.search``)؛ مسار المفقودات ليس
    له مصدر معرض بعد فتُعاد قائمة فارغة تلقائياً عبر ``.get``.
    """
    _imgs = item.get("comp_images", []) or []
    return {
        "store": str(item.get(keys["store"], "") or ""),
        "name": str(item.get(keys["name"], "") or ""),
        "price": _to_float(item.get(keys["price"], 0)),
        "image": str(item.get(keys["image"], "") or ""),
        "link": str(item.get(keys["link"], "") or ""),
        "size": str(item.get(keys["size"], "") or ""),
        "score": _to_float(item.get(keys["score"], 0)),
        "images": [str(u) for u in _imgs if u] if isinstance(_imgs, list) else [],
    }


def parse_competitor_details(row: Any) -> list[dict[str, Any]]:
    """يوحّد منافسي الصف (مسار المفقودات أو التسعير) في قائمة مرتّبة تصاعدياً بالسعر.

    آمن تماماً: None/NaN/JSON/list/ndarray/مفقود ⇒ قائمة (فارغة عند التعذّر).
    """
    for column, keys in ((COL_COMP_DETAILS, _KEYS_AR), (_COL_ALL_COMP, _KEYS_EN)):
        items = _coerce_list(_get(row, column, None))
        details = [_norm_detail(d, keys) for d in items if isinstance(d, dict)]
        if details:
            details.sort(key=lambda d: d["price"] or 1e9)
            return details
    return []


def build_card(row: Any) -> CardView:
    """يبني نموذج بطاقة من صف نتيجة (يدمج المسارين، بلا تبعية Streamlit)."""
    details = parse_competitor_details(row)
    cheapest = details[0] if details else {}
    comp_image = str(_get(row, COL_COMP_IMAGE)) or cheapest.get("image", "")
    return CardView(
        our_name=str(_get(row, COL_OUR_NAME)),
        our_price=_to_float(_get(row, COL_OUR_PRICE, 0)),
        our_sku=str(_get(row, COL_OUR_ID)),
        our_brand=str(_get(row, COL_BRAND)),
        our_size=str(_get(row, COL_SIZE)),
        our_type=str(_get(row, COL_TYPE)),
        our_image=_first_url(_get(row, _COL_OUR_IMAGE)),
        our_link=our_product_url_from_row(row),
        comp_name=str(_get(row, COL_COMP_NAME)) or cheapest.get("name", ""),
        comp_price=_to_float(_get(row, COL_COMP_PRICE, 0)) or cheapest.get("price", 0.0),
        comp_store=str(_get(row, COL_COMP_STORE)) or cheapest.get("store", ""),
        comp_image=comp_image,
        comp_link=competitor_product_url_from_row(row) or cheapest.get("link", ""),
        diff=_to_float(_get(row, COL_DIFF, 0)),
        match_pct=_to_float(_get(row, COL_MATCH_RATIO, 0)),
        comp_images=cheapest.get("images", []),
    )


def _q(text: Any) -> str:
    """ترميز نص استعلام آمن لرابط بحث."""
    return urllib.parse.quote_plus(str(text or "").strip())


def our_page_url(card: CardView) -> str:
    """رابط صفحة منتجنا: المباشر إن توفّر (``رابط_منتجنا``/مرادفاته)، وإلا بحث ``site:mahwous.com``.

    عند رفع كتالوج يحمل عمود رابط المنتج يُستخدم الرابط الحقيقي مباشرةً؛ وعند غيابه
    يُستخدم بحث مُوجَّه يصل لصفحة المنتج بدل رابط مكسور. آمن: بلا اسم ⇒ سلسلة فارغة.
    """
    if card.our_link.lower().startswith("http"):
        return card.our_link
    name = card.our_name.strip()
    return _MARKET_SEARCH + _q(f"site:{_OUR_DOMAIN} {name}") if name else ""


def card_tool_links(card: CardView) -> list[tuple[str, str]]:
    """أدوات إضافية للبطاقة (بحث سوقي واسع). خالصة قابلة للاختبار.

    التنقّل لصفحتَي المنتج (لدينا/المنافس) صار بالنقر على الاسم مباشرةً
    (``our_page_url``/``comp_link``) ⇒ هنا «🌐 بحث في السوق» فقط (مقارنة أسعار) تفادياً للتكرار.
    """
    name = (card.our_name or card.comp_name).strip()
    return [("🌐 بحث في السوق", _MARKET_SEARCH + _q(name))] if name else []
