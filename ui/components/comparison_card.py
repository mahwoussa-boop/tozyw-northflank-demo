"""ui/components/comparison_card.py — بطاقة مقارنة «حَلَبة» (Head-to-Head Arena).

التنظيم المطلوب:
  • حَلَبة علوية: بطاقتنا (مربّعة) VS أرخص منافس (مربّعة).
      - اسم منتجنا (رابط) فوق بطاقتنا، واسم المنافس كاملاً (رابط) فوق بطاقته.
      - الصور بحجم صحيح (مربّع + object-fit:contain لإظهار العطر كاملاً).
      - الأسعار في الوسط بين البطاقتين مع شارة VS + فرق السعر.
      - تحت بطاقتنا: رقم المنتج No + ماركة (صغير). وتحت المنافس: المتجر + التطابق.
      - الاسم والحجم معاً في سطر الاسم، بلا تكرار لأي معلومة.
  • أسفل الحَلَبة: «باقي المنافسين» بعرض طولي كامل (صف لكل منافس مهما كثروا):
      صورة + اسم كامل (رابط) + سعر + معلومات وإشارات.
  • الأزرار تُرسم أصلاً بـ Streamlit (دوال الصفحة) أسفل البطاقة — ليست في الـHTML.

ألوان ثابتة: بطاقتنا نيلي ``#818CF8``، المنافس كهرماني ``#F59E0B``؛ ولون
حدّ البطاقة العلوي = لون القسم (``accent``) لتوحيد التعرّف بين الصفحات.

⚠️ استثناء «لا HTML» مقصود (طُلب صراحةً). المكوّن معزول: لا يلمس
``product_card.py``. نمط نقي + غلاف ``render_*`` كسول. الروابط في الأسماء.
"""
from __future__ import annotations

import html
import urllib.parse
from typing import Any, Mapping, Sequence

# ── ألوان (من المشروع القديم styles.py) ──
_CARD = "#111827"
_BORDER = "#1F2937"
_OURS = "#818CF8"
_COMP = "#F59E0B"
_NAME = "#E2E8F0"
_MUTED = "#94A3B8"
_RED = "#EF4444"      # المنافس أرخص (تهديد)
_GREEN = "#10B981"    # نحن أرخص (ميزة)
_YELLOW = "#F59E0B"   # متساوٍ
_MISSING = "#0EA5E9"  # لون قسم المفقودات
_REVIEW = "#3B82F6"   # محتمل (مراجعة)
_SEARCH = "https://www.google.com/search?q="


# ════════════════════════════════════════════════════════════════════
#  مساعدات نقية
# ════════════════════════════════════════════════════════════════════
def _esc(value: Any) -> str:
    return "" if value is None else html.escape(str(value), quote=True)


def _f(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: Any) -> str:
    return f"{_f(value):,.0f}"


def _clean(value: Any) -> str:
    """نص آمن مُقلَّم (يحوّل nan/none/<NA> إلى فراغ)."""
    s = str(value or "").strip()
    return "" if s.lower() in ("nan", "none", "<na>") else s


def _name_with_size(name: Any, size: Any) -> str:
    """الاسم + الحجم في سطر واحد (الحجم بلون باهت بعد الاسم)."""
    nm = _esc(_clean(name) or "—")
    sz = _clean(size)
    if sz:
        unit = "" if any(u in sz for u in ("مل", "ml", "ML")) else " مل"
        nm += f' <span style="color:{_MUTED};font-weight:400">· {_esc(sz)}{unit}</span>'
    return nm


def _link(inner_html: str, url: Any, color: str) -> str:
    """يلفّ نصاً برابط HTML صريح — رابط مباشر إن توفّر، وإلا بحث Google كبديل.

    دائماً ``<a target="_blank">`` لا ماركداون — يفتح في صفحة جديدة ولا يُكسر.
    """
    u = _clean(url)
    if u.lower().startswith(("http://", "https://")):
        href = _esc(u)
    else:
        # بديل آمن: بحث Google بالاسم (يستخرج النص الخام من inner_html)
        import re as _re
        plain = _re.sub(r'<[^>]+>', '', inner_html).strip()
        if plain and plain != '—':
            href = _esc(f"{_SEARCH}{urllib.parse.quote_plus(plain)}")
        else:
            return f'<span style="color:{color}">{inner_html}</span>'
    return (
        f'<a href="{href}" target="_blank" rel="noopener" '
        f'style="text-decoration:none;color:{color}">{inner_html} 🔗</a>'
    )


def _img(url: Any, size: int, *, border: str, radius: int = 10) -> str:
    """مربّع صورة بحجم صحيح (contain يُظهر العطر كاملاً)، مع بديل 🧴."""
    u = _clean(url)
    box = (
        f"width:{size}px;height:{size}px;border-radius:{radius}px;"
        f"border:1px solid {border};background:#0e1628;flex-shrink:0;"
        f"display:flex;align-items:center;justify-content:center;overflow:hidden"
    )
    if u.lower().startswith(("http://", "https://", "//")):
        return (
            f'<div style="{box}">'
            f'<img src="{_esc(u)}" loading="lazy" referrerpolicy="no-referrer" '
            f'style="max-width:100%;max-height:100%;object-fit:contain" '
            f"onerror=\"this.parentElement.innerHTML='🧴';"
            f"this.parentElement.style.fontSize='{int(size * 0.4)}px';"
            f"this.parentElement.style.color='#475569';\"/></div>"
        )
    return (
        f'<div style="{box};font-size:{int(size * 0.4)}px;color:#475569">🧴</div>'
    )


# ── توفّر المنافس (نفذت/متوفر) — مخزّن مؤقتاً على مستوى العملية ──────────
# مجموعة روابط منتجات المنافسين التي نفذت (availability=0)، تُقرأ مرّة واحدة
# وتتحدّث تلقائياً عند تغيّر زمن تعديل قاعدة المنافسين (بعد كل كشطة جديدة).
_AVAIL_CACHE: dict[str, Any] = {"sig": None, "oos": frozenset()}


def _oos_links() -> frozenset:
    """روابط منتجات المنافسين النافِذة (availability=0). آمنة تماماً وسريعة.

    تُخزَّن بمفتاح ``getmtime`` للقاعدة: استدعاء O(1) بعد أول بناء، وإعادة بناء
    تلقائية بعد أي كشطة تُعدّل القاعدة. قاعدة/عمود مفقود ⇒ مجموعة فارغة.
    """
    import os

    from conf.constants import COMPETITOR_DB_PATH

    db = str(COMPETITOR_DB_PATH)
    try:
        sig = os.path.getmtime(db)
    except OSError:
        return frozenset()
    if _AVAIL_CACHE["sig"] == sig:
        return _AVAIL_CACHE["oos"]

    import sqlite3

    oos: set[str] = set()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute(
                "PRAGMA table_info(competitor_products_store)")]
            if "availability" in cols:
                rows = con.execute(
                    "SELECT product_url FROM competitor_products_store "
                    "WHERE availability=0 AND product_url IS NOT NULL "
                    "AND product_url<>''"
                ).fetchall()
                oos = {str(r[0]).strip() for r in rows if r[0]}
        finally:
            con.close()
    except Exception:
        oos = set()
    _AVAIL_CACHE["sig"] = sig
    _AVAIL_CACHE["oos"] = frozenset(oos)
    return _AVAIL_CACHE["oos"]


def _avail_badge(comp: Mapping[str, Any]) -> str:
    """شارة توفّر المنافس (🔴 نفذت / 🟢 متوفر). نقية.

    تظهر فقط حين يكون التوفّر معروفاً (المفتاح ``out_of_stock`` مضبوط بوضوح) —
    وإلا فراغ (لا نخمّن توفّراً غير مؤكَّد).
    """
    oos = comp.get("out_of_stock") if hasattr(comp, "get") else None
    if oos is None:
        return ""
    color, text = (_RED, "🔴 نفذت") if oos else (_GREEN, "🟢 متوفر")
    return (
        f'<span style="font-size:.68rem;font-weight:700;color:{color};'
        f'background:{color}1F;border:1px solid {color}55;border-radius:7px;'
        f'padding:1px 7px;white-space:nowrap">{text}</span>'
    )


# ── قِدَم بيانات المنافس (طبقة عرض فوق حارس الطزاجة القائم في opportunity_service) ──
_AGE_CACHE: dict[str, Any] = {"sig": None, "ages": {}}


def _data_ages() -> dict[str, float]:
    """خريطة رابط_منتج ← قِدَم البيانات بالأيام (نفس صيغة ``opportunity_service``:
    ``julianday('now') - julianday(updated_at)``). مخزَّنة بمفتاح ``mtime`` كـ``_oos_links``."""
    import os

    from conf.constants import COMPETITOR_DB_PATH

    db = str(COMPETITOR_DB_PATH)
    try:
        sig = os.path.getmtime(db)
    except OSError:
        return {}
    if _AGE_CACHE["sig"] == sig:
        return _AGE_CACHE["ages"]

    import sqlite3

    ages: dict[str, float] = {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT product_url, MIN(julianday('now') - julianday(updated_at)) "
                "FROM competitor_products_store "
                "WHERE product_url IS NOT NULL AND product_url<>'' "
                "GROUP BY product_url"
            ).fetchall()
            ages = {str(r[0]).strip(): float(r[1]) for r in rows if r[0] and r[1] is not None}
        finally:
            con.close()
    except Exception:
        ages = {}
    _AGE_CACHE["sig"] = sig
    _AGE_CACHE["ages"] = ages
    return _AGE_CACHE["ages"]


def _data_age_badge(comp: Mapping[str, Any]) -> str:
    """شارة قِدَم البيانات (🕒) — تظهر فقط عند تجاوز عتبة الطزاجة، لا على الطازج
    (تفادي ضجيج بصري). تستخدم نفس عتبة/صياغة حارس الطزاجة في ``opportunity_service``."""
    from services.opportunity_service import STALE_AFTER_DAYS, format_data_age

    age = comp.get("data_age_days") if hasattr(comp, "get") else None
    if age is None or age <= STALE_AFTER_DAYS:
        return ""
    return (
        f'<span style="font-size:.68rem;font-weight:700;color:{_MUTED};'
        f'background:{_MUTED}1F;border:1px solid {_MUTED}55;border-radius:7px;'
        f'padding:1px 7px;white-space:nowrap" title="بيانات المنافس لم تتحدّث حديثاً">'
        f'🕒 {_esc(format_data_age(age))}</span>'
    )


def _enrich_availability(comps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """يضبط ``out_of_stock`` و``data_age_days`` لكل منافس له رابط. يعدّل ويُعيد."""
    oos = _oos_links()
    ages = _data_ages()
    for c in comps:
        link = _clean(c.get("link"))
        if link:
            c["out_of_stock"] = link in oos
            if link in ages:
                c["data_age_days"] = ages[link]
    return comps


def _comp_link(comp: Mapping[str, Any]) -> str:
    """رابط المنافس الآمن: صفحة المنتج إن كان متوفراً، وإلا بحث بالاسم (لأن صفحة
    المنتج النافذ تُحذف عند المتجر فتعطي 404). يمنع «الرابط يعطي خطأ» للنافذة."""
    if comp.get("out_of_stock"):
        name = _clean(comp.get("name"))
        return f"{_SEARCH}{urllib.parse.quote_plus(name)}" if name else ""
    return _clean(comp.get("link"))


def _diff_text(our_price: float, comp_price: float) -> tuple[str, str]:
    """(لون، نص) لفرق سعرنا عن منافس — أحمر=أغلى، أخضر=أرخص."""
    if our_price <= 0 or comp_price <= 0:
        return (_MUTED, "—")
    diff = our_price - comp_price
    pct = abs(diff) / comp_price * 100
    if diff > 0:
        return (_RED, f"أغلى بـ {_money(diff)} (+{pct:.0f}%)")
    if diff < 0:
        return (_GREEN, f"أرخص بـ {_money(-diff)} (−{pct:.0f}%)")
    return (_YELLOW, "متساوٍ")


# ════════════════════════════════════════════════════════════════════
#  الحَلَبة (بطاقتنا VS أرخص منافس)
# ════════════════════════════════════════════════════════════════════
def _gallery_strip(images: Any, *, size: int = 26, max_thumbs: int = 4) -> str:
    """شريط صور مصغّرة إضافية للمنافس (ج3 خ2، عرض صرف) — يظهر فقط حين ≥2 صورة
    فعلية (نفس عتبة «معرض مجدٍ» من استطلاع ج3 خ1)؛ صورة واحدة لا تستحق شريطاً
    (الصورة الرئيسية تكفي)."""
    imgs = [str(u).strip() for u in (images or []) if str(u or "").strip()]
    if len(imgs) < 2:
        return ""
    thumbs = "".join(
        f'<img src="{_esc(u)}" loading="lazy" referrerpolicy="no-referrer" '
        f'style="width:{size}px;height:{size}px;object-fit:cover;border-radius:5px;'
        f'border:1px solid {_BORDER}" onerror="this.style.display=\'none\'"/>'
        for u in imgs[:max_thumbs]
    )
    extra = len(imgs) - max_thumbs
    more = f'<span style="font-size:.62rem;color:{_MUTED}">+{extra}</span>' if extra > 0 else ""
    return (
        f'<div style="display:flex;gap:4px;margin-top:4px;align-items:center">'
        f'{thumbs}{more}</div>'
    )


def _arena_side(name_html: str, url: Any, img: str, sub: str, *,
                color: str, css_class: str) -> str:
    return (
        f'<div class="ar-side {css_class}">'
        f'<div class="ar-nm">{_link(name_html, url, color)}</div>'
        f'<div class="ar-sq">{img}</div>'
        f'<div class="ar-sub">{sub}</div>'
        "</div>"
    )


def _arena(our: Mapping[str, Any], comp: Mapping[str, Any], currency: str) -> str:
    our_price = _f(our.get("price"))
    comp_price = _f(comp.get("price"))
    color, diff = _diff_text(our_price, comp_price)

    # بطاقتنا: اسم+حجم فوق، صورة، ثم No + ماركة تحت (لا تكرار).
    our_id = _clean(our.get("id")) or _clean(our.get("no"))
    our_sub_bits = []
    if our_id:
        our_sub_bits.append(f"No: {_esc(our_id)}")
    brand = _clean(our.get("brand"))
    if brand:
        our_sub_bits.append(f"🏷️ {_esc(brand)}")
    our_side = _arena_side(
        _name_with_size(our.get("name"), our.get("size")), our.get("link"),
        _img(our.get("image"), 132, border=_OURS, radius=12),
        " · ".join(our_sub_bits) or "&nbsp;",
        color=_NAME, css_class="ours",
    )

    # المنافس (الأرخص): اسم كامل فوق، صورة، ثم المتجر + التطابق تحت.
    score = _f(comp.get("score"))
    comp_sub_bits = [f"🏬 {_esc(_clean(comp.get('store')) or 'منافس')}"]
    if score > 0:
        comp_sub_bits.append(f"🎯 {score:.0f}%")
    ab = _avail_badge(comp)
    if ab:
        comp_sub_bits.append(ab)
    da = _data_age_badge(comp)
    if da:
        comp_sub_bits.append(da)
    comp_side = _arena_side(
        _name_with_size(comp.get("name"), comp.get("size")), _comp_link(comp),
        _img(comp.get("image"), 132, border=_COMP, radius=12),
        " · ".join(comp_sub_bits) + _gallery_strip(comp.get("images")),
        color=_COMP, css_class="comp",
    )

    center = (
        '<div class="ar-vs">'
        '<div class="ar-prices">'
        '<div class="ar-pcol">'
        '<div class="ar-pl">سعرنا</div>'
        f'<div class="ar-p ours">{_money(our_price)}<small>{_esc(currency)}</small></div>'
        '</div>'
        '<div class="ar-badge">VS</div>'
        '<div class="ar-pcol">'
        '<div class="ar-pl">👑 الأرخص</div>'
        f'<div class="ar-p comp">{_money(comp_price)}<small>{_esc(currency)}</small></div>'
        '</div>'
        '</div>'
        f'<div class="ar-diff" style="color:{color};background:{color}1F;'
        f'border:1px solid {color}44">{diff}</div>'
        "</div>"
    )
    return f'<div class="ar">{our_side}{center}{comp_side}</div>'


# ════════════════════════════════════════════════════════════════════
#  باقي المنافسين (عرض طولي كامل — صف لكل منافس)
# ════════════════════════════════════════════════════════════════════
def _vl_row(comp: Mapping[str, Any], our_price: float, currency: str) -> str:
    price = _f(comp.get("price"))
    color, _ = _diff_text(our_price, price)
    diff = our_price - price if (our_price > 0 and price > 0) else 0.0
    if diff > 0:
        dh = f'<div class="vl-d" style="color:{_RED}">+{_money(diff)} ▼</div>'
    elif diff < 0:
        dh = f'<div class="vl-d" style="color:{_GREEN}">−{_money(-diff)} ▲</div>'
    else:
        dh = f'<div class="vl-d" style="color:{_YELLOW}">=</div>'

    meta = [f"🏬 {_esc(_clean(comp.get('store')) or 'منافس')}"]
    size = _clean(comp.get("size"))
    if size:
        meta.append(f"📏 {_esc(size)}")
    score = _f(comp.get("score"))
    if score > 0:
        meta.append(f"🎯 {score:.0f}%")
    ab = _avail_badge(comp)
    if ab:
        meta.append(ab)
    da = _data_age_badge(comp)
    if da:
        meta.append(da)

    name = _link(_esc(_clean(comp.get("name")) or "—"), _comp_link(comp), _NAME)
    gallery = _gallery_strip(comp.get("images"))
    return (
        '<div class="vl-r">'
        f'<div class="vl-sq">{_img(comp.get("image"), 54, border=_BORDER, radius=8)}</div>'
        f'<div class="vl-i"><div class="vl-n">{name}</div>'
        f'<div class="vl-m">{" · ".join(meta)}</div>{gallery}</div>'
        '<div class="vl-pr">'
        f'<div class="vl-pp" style="color:{_COMP}">{_money(price)} '
        f'<span style="font-size:.6rem;color:{_MUTED}">{_esc(currency)}</span></div>'
        f"{dh}</div>"
        "</div>"
    )


# ════════════════════════════════════════════════════════════════════
#  الأنماط (تُحقن مرّة واحدة) — فراغات ضيّقة + خطوط كبيرة
# ════════════════════════════════════════════════════════════════════
def comparison_card_css() -> str:
    return f"""<style>
.mhw-card,.mhw-card *{{font-family:'Tajawal',sans-serif;box-sizing:border-box}}
.mhw-card{{background:linear-gradient(180deg,{_CARD} 0%,#0e1118 100%);border:1px solid {_BORDER};
  border-top:3px solid var(--ac,{_OURS});border-radius:16px;margin:10px 0;
  overflow:hidden;direction:rtl;color:{_NAME};
  transition:box-shadow .3s ease,transform .2s ease}}
.mhw-card:hover{{box-shadow:0 8px 32px rgba(0,0,0,.4);transform:translateY(-2px)}}

/* ── شارة العروض (📦) — رأس البطاقة، نيلي متدرّج، لا يكسر الحَلَبة ── */
.mhw-head{{display:flex;justify-content:flex-start;align-items:center;
  gap:8px;padding:12px 14px 0}}
.mhw-offers-badge{{background:linear-gradient(135deg,#6366F1,#818CF8);
  color:#fff;font-size:.76rem;font-weight:800;padding:4px 12px;border-radius:14px;
  display:inline-flex;align-items:center;gap:6px;white-space:nowrap;
  box-shadow:0 2px 8px rgba(99,102,241,.35)}}
.mhw-crit-badge{{font-size:.76rem;font-weight:800;padding:4px 12px;border-radius:14px;
  display:inline-flex;align-items:center;gap:5px;white-space:nowrap;cursor:help}}
.mhw-pulse{{width:8px;height:8px;border-radius:50%;background:#FDE047;flex-shrink:0;
  box-shadow:0 0 0 0 rgba(253,224,71,.7);animation:mhwPulse 1.6s infinite}}
@keyframes mhwPulse{{
  0%{{box-shadow:0 0 0 0 rgba(253,224,71,.7)}}
  70%{{box-shadow:0 0 0 6px rgba(253,224,71,0)}}
  100%{{box-shadow:0 0 0 0 rgba(253,224,71,0)}}
}}

.ar{{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;padding:14px;
  align-items:start;min-width:0;overflow:hidden}}
.ar-side{{display:flex;flex-direction:column;gap:8px;text-align:center;min-width:0;overflow:hidden}}
.ar-nm{{font-size:1.06rem;font-weight:700;line-height:1.4;min-height:2.8em;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.ar-nm a:hover{{opacity:.85}}
.ar-sq{{height:140px;display:flex;align-items:center;justify-content:center}}
.ar-sq>div{{width:140px;height:140px}}
.ar-side.ours .ar-sq>div{{border-color:{_OURS}}}
.ar-side.comp .ar-sq>div{{border-color:{_COMP}}}
.ar-sub{{font-size:.76rem;color:{_MUTED}}}

.ar-vs{{display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:8px;height:100%;padding-top:1.2em}}
.ar-prices{{display:flex;align-items:center;justify-content:center;gap:1.8rem}}
.ar-pcol{{text-align:center;min-width:0}}
.ar-pl{{font-size:.72rem;color:{_MUTED};margin-bottom:3px}}
.ar-p{{font-size:2.2rem;font-weight:900;line-height:1.1}}
.ar-p small{{font-size:.64rem;color:{_MUTED};margin-inline-start:2px}}
.ar-p.ours{{color:{_OURS}}}
.ar-p.comp{{color:{_COMP}}}
.ar-badge{{font-size:1.1rem;font-weight:900;color:{_MUTED};background:#0e1628;
  border:1px solid {_BORDER};border-radius:50%;width:44px;height:44px;
  display:flex;align-items:center;justify-content:center;flex-shrink:0}}
.ar-diff{{font-size:.84rem;font-weight:700;padding:4px 14px;border-radius:10px;margin-top:6px}}

.vl{{padding:4px 14px 12px}}
.vl-t{{font-size:.88rem;font-weight:700;color:{_MUTED};margin:4px 0 8px;
  border-top:1px dashed {_BORDER};padding-top:10px}}
.vl-r{{display:flex;align-items:center;gap:12px;padding:8px 10px;
  background:rgba(15,23,42,.6);border:1px solid {_BORDER};border-radius:12px;margin:6px 0;
  transition:background .15s}}
.vl-r:hover{{background:rgba(15,23,42,.85)}}
.vl-sq>div{{width:56px;height:56px}}
.vl-i{{flex:1;min-width:0}}
.vl-n{{font-size:.98rem;font-weight:700;color:{_NAME};line-height:1.3;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.vl-n a:hover{{opacity:.85}}
.vl-m{{font-size:.78rem;color:{_MUTED};margin-top:3px;display:flex;gap:10px;flex-wrap:wrap}}
.vl-pr{{text-align:center;min-width:90px;flex-shrink:0}}
.vl-pp{{font-size:1.22rem;font-weight:900;line-height:1.1}}
.vl-d{{font-size:.76rem;font-weight:700;margin-top:2px}}
.mhw-empty{{padding:16px;text-align:center;color:{_MUTED};font-size:.92rem}}

/* ── بطاقة المفقودات (منتج مفقود + إشارات) ── */
.ms{{display:flex;align-items:flex-start;gap:12px;padding:14px}}
.ms-sq>div{{width:108px;height:108px}}
.ms-i{{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}}
.ms-badges{{display:flex;gap:7px;flex-wrap:wrap}}
.ms-bdg{{font-size:.74rem;font-weight:700;padding:3px 10px;border-radius:10px}}
.ms-nm{{font-size:1.1rem;font-weight:700;color:{_NAME};line-height:1.4;
  word-wrap:break-word}}
.ms-nm a:hover{{opacity:.85}}
.ms-meta{{font-size:.82rem;color:{_MUTED};display:flex;gap:10px;flex-wrap:wrap}}
.ms-rev{{font-size:.8rem;font-weight:700;color:#60A5FA;
  background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.35);
  border-radius:10px;padding:5px 10px}}
.ms-rev span{{color:{_MUTED};font-weight:400}}
.ms-stores{{font-size:.76rem;color:{_MUTED}}}
.ms-pr{{text-align:center;min-width:100px;flex-shrink:0;display:flex;
  flex-direction:column;gap:4px;align-items:center}}
.ms-pp{{font-size:1.6rem;font-weight:900;color:{_COMP};line-height:1.05}}
.ms-pp small{{font-size:.58rem;color:{_MUTED};margin-inline-start:2px}}
.ms-sg{{font-size:.74rem;color:{_GREEN};font-weight:700}}
.ms-tag{{font-size:.74rem;font-weight:700;padding:3px 10px;border-radius:10px;margin-top:3px}}

@media (max-width:640px){{
  .ar{{grid-template-columns:1fr;gap:8px}}
  .ar-vs{{padding-top:8px}}
  .ar-prices{{gap:1rem}}
  .ar-p{{font-size:1.8rem}}
  .ar-badge{{width:36px;height:36px;font-size:.92rem}}
  .ar-sq>div{{width:120px;height:120px}}
}}
@media (max-width:400px){{
  .ar-prices{{flex-wrap:wrap;gap:.6rem}}
  .ar-sq>div{{width:100px;height:100px}}
}}
</style>
"""


# ════════════════════════════════════════════════════════════════════
#  شارة العروض (📦) — رأس مشترك للبطاقتين
# ════════════════════════════════════════════════════════════════════
def _crit_badge(criticality: Any) -> str:
    """شارة الحرجية (🔴 حرج/🟡 متوسط/🟢 عادي) من ``(label, color, tooltip)`` أو ''.

    لا تُعرض درجة «عادي» (🟢) لتقليل الضوضاء — الشارة تلفت النظر للحرج/المتوسط فقط.
    """
    if not criticality:
        return ""
    label, color, tooltip = criticality
    if not label or "عادي" in label:
        return ""
    title = f' title="{_esc(tooltip)}"' if tooltip else ""
    return (
        f'<span class="mhw-crit-badge"{title} '
        f'style="color:{color};background:{color}1F;border:1px solid {color}66">'
        f"{_esc(label)}</span>"
    )


def _offers_badge(text: str, *, pulse: bool = False, criticality: Any = None) -> str:
    """يبني رأس البطاقة بشارة العروض النيلية + شارة الحرجية (إن وُجدت).

    ``pulse`` يضيف نقطة مضيئة (العدد قد يزيد بعد مراجعة الذكاء الاصطناعي).
    ``criticality`` = ``(label, color, tooltip)`` لشارة الأهمية القصوى.
    """
    dot = '<span class="mhw-pulse"></span>' if pulse else ""
    return (
        '<div class="mhw-head">'
        f'<span class="mhw-offers-badge">📦 {text}{dot}</span>'
        f"{_crit_badge(criticality)}"
        "</div>"
    )


# ════════════════════════════════════════════════════════════════════
#  بناء HTML كامل (نقي، قابل للاختبار بلا Streamlit)
# ════════════════════════════════════════════════════════════════════
def build_comparison_card_html(
    our: Mapping[str, Any],
    competitors: Sequence[Mapping[str, Any]],
    *,
    currency: str = "ر.س",
    accent: str = _OURS,
    pending_review: bool = False,
    criticality: Any = None,
) -> str:
    """يبني HTML البطاقة كاملة (حَلَبة + باقي المنافسين). دالة نقية."""
    our = our or {}
    comps = sorted(
        [c for c in (competitors or []) if c],
        key=lambda c: _f(c.get("price")) or 1e18,
    )
    our_price = _f(our.get("price"))

    if not comps:
        # بطاقة منتج منفرد (بلا منافسين) — تعرض تفاصيل منتجنا + رسالة.
        our_price_v = _f(our.get("price"))
        our_id = _clean(our.get("id")) or _clean(our.get("no"))
        sub_bits: list[str] = []
        if our_id:
            sub_bits.append(f"No: {_esc(our_id)}")
        brand_v = _clean(our.get("brand"))
        if brand_v:
            sub_bits.append(f"🏷️ {_esc(brand_v)}")
        solo = (
            '<div class="ar" style="grid-template-columns:1fr auto">'
            + _arena_side(
                _name_with_size(our.get("name"), our.get("size")),
                our.get("link"),
                _img(our.get("image"), 132, border=accent, radius=12),
                " · ".join(sub_bits) or "&nbsp;",
                color=_NAME, css_class="ours",
            )
            + '<div class="ar-vs" style="padding-top:2em">'
            + (f'<div class="ar-p ours">{_money(our_price_v)}'
               f'<small>{_esc(currency)}</small></div>' if our_price_v else '')
            + '<div class="ar-diff" style="color:#9CA3AF;background:#9CA3AF1F;'
              'border:1px solid #9CA3AF44">⚪ لا يوجد تطابق</div>'
            + '</div></div>'
        )
        body = solo
    else:
        arena = _arena(our, comps[0], currency)
        rest = comps[1:]
        if rest:
            rows = "".join(_vl_row(c, our_price, currency) for c in rest)
            body = (
                f"{arena}"
                f'<div class="vl"><div class="vl-t">👥 باقي المنافسين ({len(rest)})'
                f"</div>{rows}</div>"
            )
        else:
            body = arena

    # شارة العروض: عدد المنافسين + 1 (منتجنا) + شارة الحرجية إن وُجدت.
    badge = _offers_badge(
        f"العروض: {len(comps) + 1}", pulse=pending_review, criticality=criticality,
    )
    card = f'<div class="mhw-card" style="--ac:{accent}">{badge}{body}</div>'
    # حارس الأسطر الفارغة (يمنع تسرّب HTML كنص — درس styles._no_blank_lines).
    return "".join(line for line in card.split("\n") if line.strip())


# ════════════════════════════════════════════════════════════════════
#  الغلاف الرفيع (يستورد Streamlit بكسل)
# ════════════════════════════════════════════════════════════════════
def render_comparison_card(
    our: Mapping[str, Any],
    competitors: Sequence[Mapping[str, Any]],
    *,
    currency: str = "ر.س",
    accent: str = _OURS,
    inject_css: bool = True,
    pending_review: bool = False,
    criticality: Any = None,
) -> None:
    """يعرض بطاقة الحَلَبة. يحقن الأنماط مرّة واحدة لكل تشغيل صفحة."""
    import streamlit as st  # استيراد كسول → الاختبارات تبقى headless

    if inject_css and not st.session_state.get("_mhw_cmp_css"):
        st.markdown(comparison_card_css(), unsafe_allow_html=True)
        st.session_state["_mhw_cmp_css"] = True

    st.markdown(
        build_comparison_card_html(
            our, competitors, currency=currency, accent=accent,
            pending_review=pending_review, criticality=criticality,
        ),
        unsafe_allow_html=True,
    )


# ════════════════════════════════════════════════════════════════════
#  محوّل البيانات الحقيقية: صف نتيجة المحرّك → (منتجنا، منافسين)
# ════════════════════════════════════════════════════════════════════
def row_to_arena(row: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """يحوّل صفاً حقيقياً (أعمدة المحرّك العربية) إلى مدخلات بطاقة الحَلَبة.

    #PRESERVED_LOGIC: يعيد استخدام محلّلات ``product_card`` المُختبَرة
    (``build_card`` + ``parse_competitor_details`` + ``our_page_url``) فيرث
    معالجة كل أسماء الأعمدة ومساري التسعير/المفقودات وروابط المنتجات — دون
    إعادة اختراع ودون اختلاق بيانات. الرقم من عمود ``NO`` (وإلا معرف_المنتج).
    """
    from ui.components.product_card import (  # استيراد كسول (محلّلات خالصة)
        build_card, our_page_url, parse_competitor_details,
    )

    card = build_card(row)
    comps = parse_competitor_details(row)  # يحمل score الآن (مفتاح إنجليزي)
    _enrich_availability(comps)  # يضيف شارة 🔴 نفذت/🟢 متوفر لكل منافس له رابط

    no = row.get("NO") if hasattr(row, "get") else None
    no = "" if no is None or str(no).strip().lower() in ("nan", "none", "") else str(no)
    our = {
        "id": no or card.our_sku,
        "name": card.our_name,
        "price": card.our_price,
        "image": card.our_image,
        "link": our_page_url(card),  # رابط مباشر إن وُجد، وإلا بحث مُوجَّه (لا رابط مكسور)
        "brand": card.our_brand,
        "size": card.our_size,
    }
    return our, comps


def render_row_arena(
    row: Any, *, accent: str = _OURS, currency: str = "ر.س", inject_css: bool = True,
    pending_review: bool = False, show_criticality: bool = True,
) -> None:
    """يعرض بطاقة حَلَبة من صف نتيجة حقيقي (غلاف فوق المحوّل + render).

    يحسب شارة الحرجية من الصف نفسه (خطورة المحرك + فجوة السعر + خطّاف المخزون).
    """
    from ui.components.criticality import row_criticality_badge  # كسول (وحدة نقية)

    our, comps = row_to_arena(row)
    crit = row_criticality_badge(row) if show_criticality else None
    render_comparison_card(
        our, comps, accent=accent, currency=currency, inject_css=inject_css,
        pending_review=pending_review, criticality=crit,
    )


# ════════════════════════════════════════════════════════════════════
#  بطاقة المفقودات (منتج منافس ليس في كتالوجنا) — نفس الهوية البصرية
# ════════════════════════════════════════════════════════════════════
def _search_url(name: str) -> str:
    """رابط بحث سوقي بالاسم (بديل آمن إذ لا يوجد رابط منتج مباشر للمفقود)."""
    q = urllib.parse.quote_plus(str(name or "").strip())
    return f"{_SEARCH}{q}" if q else ""


def _restore_stores(raw: Any) -> list[str]:
    """يعيد بناء أسماء المتاجر من ``المنافسون`` المعطوب (إصلاح حتمي بلا اختلاق).

    #PRESERVED_LOGIC: بيانات missing_df تخزّن المتاجر مفصولةً بفاصلة ASCII لكنها
    انقسمت حرفاً حرفاً وأُعيد ضمّها بـ «،» (مثال: «م، ي، ن، ي ,، ع، ا، ل، م»).
    نعيد التجميع: «,» تفصل المتاجر و«،» تفصل الأحرف داخل الاسم — فنسترجع الاسم
    كاملاً (تُفقد المسافات فقط، حدّ بيانات لا اختلاق). يدعم أيضاً البيانات السليمة.
    """
    s = str(raw or "").strip()
    if not s:
        return []
    if "،" not in s:  # بيانات سليمة (أسماء مفصولة بفاصلة عادية)
        return [p.strip() for p in s.split(",") if p.strip()]
    groups: list[str] = []
    cur: list[str] = []
    for tok in s.split("،"):
        t = tok.strip()
        if t == ",":            # فاصل متاجر
            if cur:
                groups.append("".join(cur))
                cur = []
        elif t:                 # حرف داخل اسم المتجر
            cur.append(t)
    if cur:
        groups.append("".join(cur))
    return [g for g in groups if len(g) >= 2]


def build_missing_card_html(row: Any, *, accent: str = _MISSING,
                            currency: str = "ر.س", kind: str = "missing") -> str:
    """يبني HTML بطاقة منتج مفقود من صف missing_df (أعمدة عربية). دالة نقية.

    #PRESERVED_LOGIC: الأعمدة من missing_df الحيّة (منتج_المنافس/سعر_المنافس/
    صورة_المنافس/المنافس/الماركة/مستوى_الثقة/السعر_المقترح/منتج_مطابق_محتمل/
    درجة_التشابه/المنافسون/عدد_المنافسين/هو_تستر). لا تفاصيل_المنافسين هنا.
    """
    def cell(col: str) -> str:
        return _clean(row.get(col) if hasattr(row, "get") else None)

    def num(col: str) -> float:
        return _f(row.get(col) if hasattr(row, "get") else 0)

    name = cell("منتج_المنافس") or "—"
    price = num("سعر_المنافس")
    conf = cell("مستوى_الثقة")
    suggested = num("السعر_المقترح")
    count = int(num("عدد_المنافسين")) or 1
    match = cell("منتج_مطابق_محتمل")
    sim = num("درجة_التشابه")
    tester = cell("هو_تستر").lower() in ("true", "1", "نعم", "yes")

    if kind in ("new", "oos"):
        # جديد/نفذت: شارة القسم + شارة الملكية (نملكه؟) — يُعيد استخدام نفس أنماط البطاقة.
        if kind == "oos":
            cc, ct, tag = _RED, "🔴 نفذت", "🔴 نفذت"
        else:
            cc, ct, tag = _GREEN, "🆕 جديد", "🆕 جديد"
        badges = f'<span class="ms-bdg" style="color:{cc};background:{cc}1F;border:1px solid {cc}55">{ct}</span>'
        _own = cell("ownership")
        if _own == "owned":
            badges += f'<span class="ms-bdg" style="color:{_GREEN};background:{_GREEN}1F;border:1px solid {_GREEN}55">✅ لديك</span>'
        elif _own == "review":
            badges += f'<span class="ms-bdg" style="color:{_REVIEW};background:{_REVIEW}1F;border:1px solid {_REVIEW}55">🔵 محتمل لديك</span>'
        else:
            badges += f'<span class="ms-bdg" style="color:{_MISSING};background:{_MISSING}1F;border:1px solid {_MISSING}55">🆕 ليس لديك</span>'
    else:
        if conf == "green":
            cc, ct, tag = _GREEN, "🟢 مؤكد مفقود", "🆕 مفقود"
        elif conf == "review":
            cc, ct, tag = _REVIEW, "🔵 محتمل (مراجعة)", "🔵 مراجعة"
        else:
            cc, ct, tag = _MUTED, (conf or "—"), "•"
        badges = f'<span class="ms-bdg" style="color:{cc};background:{cc}1F;border:1px solid {cc}55">{ct}</span>'
    if tester:
        badges += '<span class="ms-bdg" style="color:#C084FC;background:#9333EA1F;border:1px solid #9333EA55">🧪 تستر</span>'
    # شارة التوفّر (نفذت/متوفر) — تظهر فقط حين يتوفّر رابط منافس حقيقي نطابقه.
    _real_url = cell("رابط_المنافس")
    if _real_url:
        badges += _avail_badge({"out_of_stock": _real_url in _oos_links()})
        badges += _data_age_badge({"data_age_days": _data_ages().get(_real_url)})

    store_names = _restore_stores(cell("المنافسون"))
    main_store = cell("المنافس") if len(cell("المنافس")) >= 2 else (
        store_names[0] if store_names else "")
    n_stores = len(store_names) or count

    meta = []
    if main_store:
        meta.append(f"🏬 {_esc(main_store)}")
    if cell("الماركة"):
        meta.append(f"🏷️ {_esc(cell('الماركة'))}")
    if n_stores > 1:
        meta.append(f"👥 {n_stores} متجر")

    rev = (
        f'<div class="ms-rev">🔵 يشبه منتجنا: {_esc(match)} '
        f'<span>({sim:.0f}%)</span></div>'
        if conf == "review" and match else ""
    )
    extra = store_names[1:6]
    stores = (
        f'<div class="ms-stores">🏪 يبيعه أيضاً: '
        f'{_esc("، ".join(extra))}{" …" if len(store_names) > 6 else ""}</div>'
        if extra else ""
    )
    sg = (
        f'<div class="ms-sg">سعر مقترح: {_money(suggested)} {_esc(currency)}</div>'
        if suggested > 0 else ""
    )
    comp_url = cell("رابط_المنافس") or _search_url(name)
    name_html = _link(_name_with_size(name, ""), comp_url, _NAME)

    # شارة التوفّر: عدد المتاجر (المنافسون فقط) + نبض إن كان المنتج تحت المراجعة
    # + شارة الحرجية (فجوة السعر/خطّاف المخزون) من الصف نفسه.
    from ui.components.criticality import row_criticality_badge  # كسول (وحدة نقية)

    unit = "متجر" if n_stores == 1 else "متاجر"
    offers = _offers_badge(
        f"متوفر في: {n_stores} {unit}", pulse=(conf == "review"),
        criticality=row_criticality_badge(row),
    )
    card = (
        f'<div class="mhw-card" style="--ac:{accent}">{offers}<div class="ms">'
        f'<div class="ms-sq">{_img(cell("صورة_المنافس"), 104, border=accent, radius=12)}</div>'
        '<div class="ms-i">'
        f'<div class="ms-badges">{badges}</div>'
        f'<div class="ms-nm">{name_html}</div>'
        f'<div class="ms-meta">{" · ".join(meta)}</div>'
        f"{rev}{stores}"
        "</div>"
        '<div class="ms-pr">'
        f'<div class="ms-pp">{_money(price)}<small>{_esc(currency)}</small></div>'
        f"{sg}"
        f'<div class="ms-tag" style="color:{cc};background:{cc}1F">{tag}</div>'
        "</div></div></div>"
    )
    return "".join(line for line in card.split("\n") if line.strip())


def render_missing_card(
    row: Any, *, accent: str = _MISSING, currency: str = "ر.س", inject_css: bool = True,
) -> None:
    """يعرض بطاقة منتج مفقود (نفس CSS بطاقة الحَلَبة، يُحقن مرّة واحدة)."""
    import streamlit as st

    if inject_css and not st.session_state.get("_mhw_cmp_css"):
        st.markdown(comparison_card_css(), unsafe_allow_html=True)
        st.session_state["_mhw_cmp_css"] = True

    st.markdown(
        build_missing_card_html(row, accent=accent, currency=currency),
        unsafe_allow_html=True,
    )
