"""ui/components/expert_card.py — «تيرمنال العطور» (The Bloomberg Terminal for Perfumes).

بطاقة Command Center فاخرة (Dark Theme) تعرض المنتج كلوحة تداول مصغّرة. الفلسفة
المعمارية هنا ليست تجميلية بل قياسية: **البطاقة لا تعرض أرقاماً مُمرَّرة، بل تشتقّها**.

ثلاث طبقات نقية منفصلة (تُختبَر بلا Streamlit):
  1) ``analyze(data)`` — العقل: يدقّق المنافسين (تستر/عينة/شاذ)، يفصل «الأرضية
     الموثوقة» عن «أرخص سعر خام»، يحسب المتوسط/المدى/الهامش، ويفرز تلقائياً
     (أخضر/أصفر/أحمر). دالة خالصة بالكامل.
  2) ``build_expert_card_html(data)`` — المُولّد: يحوّل خرج العقل إلى HTML/CSS.
  3) ``render_expert_card(data)`` — الغلاف الرفيع: يحقن الأنماط + يرسم أزرار
     Streamlit الفعّالة أسفل البطاقة (✅ اعتماد / ✏️ تعديل / 📥 أرشفة).

حدّ الدفعات: الرياضيات الحتمية (تدقيق/إحصاء/فرز) بذرةٌ تعمل الآن؛ ووكلاء الـ LLM
(نصّ الحكم وثقة المطابقة) يملؤون حقول ``swarm`` في Batch 2 — فإن غابت، يركّبها
العقل من الأرقام كي تبقى البطاقة كاملة دائماً.

⚠️ استثناء «لا HTML» مقصود (طُلب صراحةً): مكوّن معزول، CSS مضمّن (لا ملف خارجي)،
نمط نقي + غلاف ``render_*`` كسول — كبقية مكوّنات المشروع (انظر comparison_card.py).
"""
from __future__ import annotations

import html
import statistics
import urllib.parse
from typing import Any, Mapping, Optional, Sequence

# ════════════════════════════════════════════════════════════════════
#  لوحة الألوان (موحّدة مع comparison_card / page_header)
# ════════════════════════════════════════════════════════════════════
_OURS = "#818CF8"      # نيلي — منتجنا
_COMP = "#F59E0B"      # كهرماني — المنافس
_NAME = "#E6EDF3"
_MUTED = "#8B98A9"
_RED = "#EF4444"       # خسارة/تهديد
_GREEN = "#22C55E"     # ربح/جاهز
_MINT = "#34D399"      # توهّج السعر المقترح
_AMBER = "#F59E0B"     # مراجعة
_GOLD = "#D4AF37"      # حدّ حكم السرب
_SEARCH = "https://www.google.com/search?q="

# عتبات العقل (قابلة للضبط لاحقاً من conf.settings دون كسر التوقيع).
_TESTER_RATIO = 0.45   # سعر < 45% من وسيط السوق ⇒ يُشتبه عينة/تستر/شاذ
_MIN_FOR_AUDIT = 3     # أقل من 3 منافسين ⇒ لا تدقيق آلي (بيانات غير كافية)
_GREEN_MATCH = 95.0    # تطابق ≥ 95% شرطٌ للمنطقة الخضراء
_RED_MATCH = 70.0      # تطابق < 70% ⇒ مطابقة ضعيفة (أحمر)
_SHARP_DROP = 0.15     # هبوط مقترح > 15% عن سعرنا ⇒ يستوجب مراجعة (أصفر)
_DEFAULT_MARGIN_PCT = 15.0


# ════════════════════════════════════════════════════════════════════
#  مساعدات نقية (مكتفية ذاتياً — كنمط page_header/comparison_card)
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


def _clean(value: Any) -> str:
    s = str(value or "").strip()
    return "" if s.lower() in ("nan", "none", "<na>", "null") else s


def _money(value: Any) -> str:
    return f"{_f(value):,.0f}"


def _get(d: Any, *path: str, default: Any = None) -> Any:
    """قارئ متداخل آمن: ``_get(data, 'product', 'name')`` لا ينهار على غياب أي مفتاح."""
    cur = d
    for key in path:
        if not isinstance(cur, Mapping) or key not in cur:
            return default
        cur = cur[key]
    return cur if cur is not None else default


def _pos(value: float, lo: float, hi: float) -> float:
    """موقع نسبي (%) لقيمة على محور [lo, hi]، مقصوص داخل [3, 97] لمنع التصاق الحواف."""
    if hi <= lo:
        return 50.0
    return max(3.0, min(97.0, (value - lo) / (hi - lo) * 100.0))


def _search_url(name: str) -> str:
    q = urllib.parse.quote_plus(_clean(name))
    return f"{_SEARCH}{q}" if q else ""


def _href(url: Any, fallback_name: str = "") -> str:
    """رابط مباشر إن صحّ، وإلا بحث Google بالاسم (لا رابط مكسور أبداً)."""
    u = _clean(url)
    if u.lower().startswith(("http://", "https://", "//")):
        return _esc(u)
    return _esc(_search_url(fallback_name))


def _img(url: Any, size: int, *, border: str, radius: int = 14) -> str:
    """مربّع صورة (contain يُظهر العطر كاملاً) مع بديل 🧴 عند الفشل."""
    u = _clean(url)
    box = (
        f"width:{size}px;height:{size}px;border-radius:{radius}px;"
        f"border:1px solid {border};background:#0a0f1a;flex-shrink:0;"
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
    return f'<div style="{box};font-size:{int(size * 0.4)}px;color:#475569">🧴</div>'


# ════════════════════════════════════════════════════════════════════
#  العقل النقي: من بيانات خام → نموذج بطاقة كامل ومُشتقّ
# ════════════════════════════════════════════════════════════════════
def audit_competitors(
    competitors: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """يدقّق قائمة المنافسين: يحترم الاستبعاد الصريح ويكتشف الشاذ آلياً.

    قاعدة الكشف (حتمية، قابلة للتفسير): إن توفّر ≥ ``_MIN_FOR_AUDIT`` منافسين،
    فأي سعر موجب < ``_TESTER_RATIO`` × وسيط الأسعار يُعلَّم «عينة/تستر/شاذ»
    ويُستبعد من حساب السوق (لكنه يبقى مرئياً في الرادار مشطوباً). هذا منطق
    «المدقّق» الحتمي — وكيل الـ LLM في Batch 2 يهذّب السبب لا الرقم.
    """
    rows: list[dict[str, Any]] = []
    prices = [
        _f(c.get("price")) for c in competitors if _f(c.get("price")) > 0
    ]
    median = statistics.median(prices) if prices else 0.0
    do_auto = len(prices) >= _MIN_FOR_AUDIT and median > 0

    for c in competitors:
        price = _f(c.get("price"))
        excluded = bool(c.get("excluded"))
        is_tester = bool(c.get("is_tester"))
        reason = _clean(c.get("exclude_reason"))

        if not excluded and do_auto and 0 < price < median * _TESTER_RATIO:
            excluded, is_tester = True, True
            reason = reason or f"سعر شاذ ({price:.0f} < {_TESTER_RATIO:.0%} من الوسيط)"
        if is_tester and not reason:
            reason = "عيّنة/تستر"
        if excluded and not reason:
            reason = "مستبعد يدوياً"

        rows.append({
            "store": _clean(c.get("store")) or "متجر",
            "name": _clean(c.get("name")),
            "price": price,
            "image": _clean(c.get("image")),
            "link": _clean(c.get("link")),
            "score": _f(c.get("score")),
            "authority": _f(c.get("authority")),  # 0..1 قوة المتجر (نايس ون≈1)
            "size": _clean(c.get("size")),
            "excluded": excluded,
            "is_tester": is_tester,
            "reason": reason,
        })
    return rows


def _strategist_price(
    *, our_price: float, trusted_low: float, strongest: Optional[dict],
    margin_floor: Optional[float],
) -> tuple[float, str]:
    """تسعير «الاستراتيجي» الحتمي (بذرة Batch 2): لا «طرح ريال» أعمى.

    يعيد ``(سعر، نوع_الاستراتيجية)`` حيث النوع ∈ {no_market, hold_lead, undercut,
    floor}. ثلاث حالات يفرّق بينها خبير حقيقي:
      • **بلا سوق موثوق:** نحافظ على سعرنا (لا نختلق مرجعاً).
      • **نحن الأرخص أصلاً (hold_lead):** متصدّرون — لا نخفض أكثر فنترك ربحاً
        على الطاولة؛ نحافظ على الصدارة.
      • **نحن أغلى (undercut):** نكسر أرخص منافس موثوق بهامش يتناسب مع قوته
        (متجر قوي كنايس ون ⇒ كسر أجرأ، مجهول ⇒ تحفّظ)، دون خرق أرضية الربح أبداً.
    """
    if trusted_low <= 0:
        base = max(our_price, margin_floor or 0.0) if our_price > 0 else 0.0
        return round(base, 2), "no_market"
    if our_price > 0 and our_price <= trusted_low:
        target, kind = our_price, "hold_lead"
    else:
        authority = max(0.0, min(1.0, _f((strongest or {}).get("authority")) or 0.4))
        undercut = 0.01 + 0.03 * authority  # 1%..4% بحسب قوة أقوى منافس
        target, kind = trusted_low * (1.0 - undercut), "undercut"
    if margin_floor is not None and margin_floor > target + 0.01:
        target, kind = margin_floor, "floor"  # الأرضية رفعت السعر — الربح أولوية
    return round(target, 2), kind


def analyze(data: Mapping[str, Any]) -> dict[str, Any]:
    """العقل: يبني نموذج بطاقة كامل ومُشتقّ من بيانات خام. دالة خالصة (تُختبَر بلا UI).

    يعيد قاموساً مسطّحاً جاهزاً للرسم: المنتج، المؤشرات الأربعة، نقاط السُّلّم،
    رادار المنافسين (مع المستبعَدين)، حكم السرب الثلاثي، والفرز التلقائي.
    """
    data = data or {}
    name = _clean(_get(data, "product", "name")) or "—"
    brand = _clean(_get(data, "product", "brand"))
    size = _clean(_get(data, "product", "size"))
    concentration = _clean(_get(data, "product", "concentration"))
    image = _clean(_get(data, "product", "image"))
    link = _clean(_get(data, "product", "link"))

    our_price = _f(data.get("our_price"))
    cost = _f(data.get("cost"))
    min_margin_pct = _f(data.get("min_margin_pct")) or _DEFAULT_MARGIN_PCT
    margin_floor = round(cost * (1 + min_margin_pct / 100.0), 2) if cost > 0 else None

    audited = audit_competitors(data.get("competitors") or [])
    trusted = [c for c in audited if not c["excluded"] and c["price"] > 0]
    excluded = [c for c in audited if c["excluded"]]
    trusted_prices = [c["price"] for c in trusted]
    raw_prices = [c["price"] for c in audited if c["price"] > 0]

    raw_low = min(raw_prices) if raw_prices else 0.0
    trusted_low = min(trusted_prices) if trusted_prices else 0.0
    market_avg = round(statistics.mean(trusted_prices), 0) if trusted_prices else 0.0
    spread = (max(trusted_prices) - min(trusted_prices)) if trusted_prices else 0.0

    # أقوى منافس موثوق = الأعلى سلطةً (وعند التعادل: الأقرب لسعرنا) — «نايس ون».
    strongest = None
    if trusted:
        strongest = max(
            trusted,
            key=lambda c: (c["authority"], -abs(c["price"] - our_price)),
        )

    # السعر المقترح: من البيانات (Batch 2/LLM) إن وُجد، وإلا يحسبه الاستراتيجي.
    provided = data.get("suggested_price")
    if provided is not None and _f(provided) > 0:
        suggested, strategy = round(_f(provided), 2), "provided"
    else:
        suggested, strategy = _strategist_price(
            our_price=our_price, trusted_low=trusted_low,
            strongest=strongest, margin_floor=margin_floor,
        )

    margin_val = round(suggested - cost, 2) if cost > 0 and suggested > 0 else None
    margin_pct = (
        round((suggested - cost) / suggested * 100.0, 1)
        if cost > 0 and suggested > 0 else None
    )
    margin_ok = margin_floor is None or suggested >= margin_floor

    # ثقة المطابقة: من «المعرّف» (Batch 2) إن وُجدت، وإلا أعلى درجة تطابق متاحة.
    confidence = _f(_get(data, "swarm", "identifier", "confidence"))
    if confidence <= 0 and trusted:
        confidence = max((c["score"] for c in trusted), default=0.0)

    # نطاق المحور: من السوق الموثوق فقط (+ علاماتنا/الأرضية) — لا تشوّهه الشواذ.
    # المستبعَدون (تستر/عينة) يُقصّون لحافة السُّلّم الباهتة بدل سحب المحور كله.
    domain_vals = [v for v in (
        *trusted_prices, our_price, suggested, market_avg,
        margin_floor or 0.0,
    ) if v and v > 0]
    lo = min(domain_vals) if domain_vals else 0.0
    hi = max(domain_vals) if domain_vals else 1.0

    drop_pct = ((our_price - suggested) / our_price) if our_price > 0 else 0.0
    triage = _triage(confidence, margin_ok, drop_pct, bool(trusted))

    return {
        "name": name, "brand": brand, "size": size,
        "concentration": concentration, "image": image, "link": link,
        "our_price": our_price, "cost": cost, "min_margin_pct": min_margin_pct,
        "margin_floor": margin_floor,
        "raw_low": raw_low, "trusted_low": trusted_low,
        "market_avg": market_avg, "spread": spread,
        "suggested": suggested, "margin_val": margin_val,
        "margin_pct": margin_pct, "margin_ok": margin_ok,
        "confidence": round(confidence, 0),
        "trusted": trusted, "excluded": excluded, "audited": audited,
        "strongest": strongest,
        "lo": lo, "hi": hi, "drop_pct": round(drop_pct * 100, 1),
        "strategy": strategy, "triage": triage,
        "verdict": _verdict_segments(data, strategy, confidence, excluded,
                                     strongest, margin_pct),
    }


def _triage(confidence: float, margin_ok: bool, drop_pct: float,
            has_market: bool) -> dict[str, str]:
    """الفرز التلقائي → (key, color, label, reason). أخضر=جاهز، أصفر=مراجعة، أحمر=خطر."""
    if not has_market:
        return {"key": "solo", "color": _MUTED, "label": "⚪ بلا سوق",
                "reason": "لا منافسون موثوقون — تسعير مستقل بقرار بشري"}
    if confidence < _RED_MATCH:
        return {"key": "red", "color": _RED, "label": "🔴 مطابقة ضعيفة",
                "reason": "الثقة منخفضة — قرار بشري إلزامي قبل أي تسعير"}
    if not margin_ok:
        return {"key": "red", "color": _RED, "label": "🔴 يخترق أرضية الربح",
                "reason": "السعر المقترح أدنى من حدّ الهامش الآمن"}
    if confidence >= _GREEN_MATCH and drop_pct <= _SHARP_DROP:
        return {"key": "green", "color": _GREEN, "label": "🟢 جاهز للاعتماد",
                "reason": "تطابق عالٍ + هامش آمن + لا هبوط حادّ"}
    return {"key": "yellow", "color": _AMBER, "label": "🟡 يتطلب مراجعة",
            "reason": "تطابق متوسط أو هبوط سعري حادّ — راجعه قبل الاعتماد"}


def _verdict_segments(
    data: Mapping[str, Any], strategy: str, confidence: float,
    excluded: list, strongest: Optional[dict], margin_pct: Optional[float],
) -> dict[str, str]:
    """يبني نصوص الوكلاء الثلاثة: من ``swarm`` (Batch 2) إن وُجدت، وإلا من الأرقام."""
    ident = _clean(_get(data, "swarm", "identifier", "note"))
    audit = _clean(_get(data, "swarm", "auditor", "note"))
    strat = _clean(_get(data, "swarm", "strategist", "note"))

    if not ident:
        ident = f"تطابق {confidence:.0f}%"
    if not audit:
        if excluded:
            names = "، ".join(c["store"] for c in excluded[:2])
            audit = f"استبعد {len(excluded)} ({names})"
        else:
            audit = "السوق نظيف — لا شواذ"
    if not strat:
        # رأس الجملة يطابق نوع الاستراتيجية فعلياً (لا «يكسر» إن كنّا متصدّرين).
        if strategy == "hold_lead":
            head = "نحافظ على الصدارة"
        elif strategy == "no_market":
            head = "تسعير مستقل"
        elif strategy == "floor":
            head = "محميّ بأرضية الربح"
        elif strongest:
            head = f"يكسر {strongest['store']}"
        else:
            head = "ضمن نطاق السوق"
        mtxt = f"هامش {margin_pct:.0f}%" if margin_pct is not None else ""
        strat = " · ".join(b for b in (head, mtxt) if b)
    return {"identifier": ident, "auditor": audit, "strategist": strat}


# ════════════════════════════════════════════════════════════════════
#  مُولّد HTML (نقي) — يستهلك خرج analyze فقط
# ════════════════════════════════════════════════════════════════════
def _ring_color(conf: float) -> str:
    if conf >= _GREEN_MATCH:
        return _GREEN
    if conf >= 80:
        return _OURS
    if conf >= _RED_MATCH:
        return _AMBER
    return _RED


def _kpi_tile(label: str, value: str, *, color: str, sub: str = "",
              sub_color: str = "", hero: bool = False, icon: str = "") -> str:
    cls = "xc-kpi xc-kpi-hero" if hero else "xc-kpi"
    sub_html = (
        f'<div class="xc-kpi-d" style="color:{sub_color or _MUTED}">{sub}</div>'
        if sub else '<div class="xc-kpi-d">&nbsp;</div>'
    )
    ic = f'<span class="xc-kpi-ic">{icon}</span>' if icon else ""
    return (
        f'<div class="{cls}" style="--k:{color}">'
        f'<div class="xc-kpi-l">{ic}{_esc(label)}</div>'
        f'<div class="xc-num xc-kpi-v">{value}<small>ر.س</small></div>'
        f"{sub_html}</div>"
    )


def _ladder(m: dict[str, Any]) -> str:
    """سُلّم عمق السوق — العنصر التوقيعي: محور سعري بنقاط المنافسين + العلامات."""
    lo, hi = m["lo"], m["hi"]
    if hi <= lo:
        return ""

    # منطقة الخسارة (تحت أرضية الربح) — تظليل أحمر من اليسار حتى الأرضية.
    floor_zone = ""
    if m["margin_floor"]:
        fp = _pos(m["margin_floor"], lo, hi)
        floor_zone = (
            f'<div class="xc-loss" style="width:{fp:.1f}%" '
            f'title="منطقة الخسارة (تحت {_money(m["margin_floor"])} ر.س)"></div>'
            f'<div class="xc-floor-line" style="left:{fp:.1f}%" '
            f'title="أرضية الربح {_money(m["margin_floor"])} ر.س"></div>'
        )

    # نقاط المنافسين: موثوق = ممتلئ كهرماني، مستبعَد = مفرّغ باهت.
    dots = ""
    for c in m["audited"]:
        if c["price"] <= 0:
            continue
        p = _pos(c["price"], lo, hi)
        crown = "👑" if c is m["strongest"] else ""
        cls = "xc-dot xc-dot-ex" if c["excluded"] else "xc-dot"
        title = (f'{c["store"]}: {_money(c["price"])} ر.س'
                 + (f' — {c["reason"]}' if c["excluded"] else ""))
        badge = (f'<span class="xc-dot-crown">{crown}</span>' if crown else "")
        dots += (f'<div class="{cls}" style="left:{p:.1f}%" '
                 f'title="{_esc(title)}">{badge}</div>')

    # العلامات: سعرنا (نيلي، خط لأعلى) + المقترح (أخضر 🎯، خط لأسفل).
    marks = ""
    if m["our_price"] > 0:
        po = _pos(m["our_price"], lo, hi)
        marks += (
            f'<div class="xc-mk xc-mk-our" style="left:{po:.1f}%" '
            f'title="سعرنا {_money(m["our_price"])} ر.س">'
            f'<span class="xc-mk-lbl">نحن</span></div>'
        )
    if m["suggested"] > 0:
        ps = _pos(m["suggested"], lo, hi)
        marks += (
            f'<div class="xc-mk xc-mk-sug" style="left:{ps:.1f}%" '
            f'title="المقترح {_money(m["suggested"])} ر.س">'
            f'<span class="xc-mk-lbl">🎯 المقترح</span></div>'
        )

    return (
        '<div class="xc-ladder">'
        '<div class="xc-ladder-h">'
        '<span>📊 سُلّم عمق السوق</span>'
        f'<span class="xc-spread">المدى {_money(lo)}–{_money(hi)} · '
        f'تشتّت {_money(m["spread"])}</span></div>'
        f'<div class="xc-track">{floor_zone}{dots}{marks}</div>'
        '<div class="xc-axis"><span>◄ أرخص</span><span>أغلى ►</span></div>'
        "</div>"
    )


def _name_size(name: str, size: str) -> str:
    """الاسم الكامل + الحجم بجانبه (الحجم بلون باهت بعد الاسم)."""
    nm = _esc(name or "—")
    sz = _clean(size)
    if sz:
        unit = "" if any(u in sz for u in ("مل", "ml", "ML")) else " مل"
        nm += f'<span class="xc-row-sz"> · {_esc(sz)}{unit}</span>'
    return nm


def _radar_row(c: dict[str, Any], our_price: float,
               strongest: Optional[dict]) -> str:
    """صف منافس كامل: صورة + الاسم الكامل + الحجم + المتجر + التطابق + السعر + الفرق."""
    price = c["price"]
    delta = our_price - price if our_price > 0 else 0.0
    if c["excluded"]:
        dcol, dtxt = _MUTED, "🚫 مستبعَد"
    elif delta > 0:
        dcol, dtxt = _RED, f"▼ {_money(delta)}"     # المنافس أرخص = تهديد
    elif delta < 0:
        dcol, dtxt = _GREEN, f"▲ {_money(-delta)}"  # نحن أرخص = ميزة
    else:
        dcol, dtxt = _AMBER, "= مطابق"

    crown = '<span class="xc-row-crown">👑</span>' if c is strongest else ""
    meta = [f'🏬 {_esc(c["store"])}']
    if c["score"] > 0:
        meta.append(f'🎯 {c["score"]:.0f}%')
    if c["excluded"] and c["reason"]:
        meta.append(_esc(c["reason"]))

    cls = "xc-row xc-row-ex" if c["excluded"] else "xc-row"
    href = _href(c["link"], c["name"] or c["store"])
    return (
        f'<a class="{cls}" href="{href}" target="_blank" rel="noopener">'
        f'<div class="xc-row-img">'
        f'{_img(c["image"], 50, border="rgba(255,255,255,.12)", radius=10)}</div>'
        '<div class="xc-row-mid">'
        f'<div class="xc-row-nm">{crown}'
        f'<span class="xc-row-nmt">{_name_size(c["name"], c["size"])}</span></div>'
        f'<div class="xc-row-meta">{" · ".join(meta)}</div></div>'
        '<div class="xc-row-pr">'
        f'<div class="xc-num xc-row-price">{_money(price)}<small>ر.س</small></div>'
        f'<div class="xc-row-d" style="color:{dcol}">{dtxt}</div></div>'
        "</a>"
    )


def _radar(m: dict[str, Any]) -> str:
    """رادار المنافسين — قائمة صفوف غنية، الموثوق أولاً ثم المستبعَد (مشطوب مع سبب)."""
    order = sorted(m["trusted"], key=lambda c: c["price"]) + m["excluded"]
    rows = "".join(_radar_row(c, m["our_price"], m["strongest"]) for c in order)
    if not rows:
        rows = '<div class="xc-empty">لا منافسين — منتج منفرد</div>'
    n_tr, n_ex = len(m["trusted"]), len(m["excluded"])
    sub = f"{n_tr} موثوق" + (f" · {n_ex} مستبعَد" if n_ex else "")
    return (
        '<div class="xc-radar">'
        f'<div class="xc-radar-h">🛰️ رادار المنافسين <small>{sub}</small></div>'
        f'<div class="xc-radar-list">{rows}</div>'
        "</div>"
    )


def _verdict_panel(m: dict[str, Any]) -> str:
    v = m["verdict"]
    return (
        '<div class="xc-verdict">'
        '<div class="xc-vd-agent"><span class="xc-vd-ic">🤖</span>'
        '<div class="xc-vd-tx"><b>المعرّف</b>'
        f'<span>{_esc(v["identifier"])}</span></div></div>'
        '<div class="xc-vd-sep"></div>'
        '<div class="xc-vd-agent"><span class="xc-vd-ic">🛡️</span>'
        '<div class="xc-vd-tx"><b>المدقّق</b>'
        f'<span>{_esc(v["auditor"])}</span></div></div>'
        '<div class="xc-vd-sep"></div>'
        '<div class="xc-vd-agent"><span class="xc-vd-ic">♟️</span>'
        '<div class="xc-vd-tx"><b>الاستراتيجي</b>'
        f'<span>{_esc(v["strategist"])}</span></div></div>'
        "</div>"
    )


def build_expert_card_html(data: Mapping[str, Any]) -> str:
    """يبني HTML البطاقة كاملة من بيانات خام (يُشغّل العقل داخلياً). دالة خالصة."""
    m = analyze(data)
    tr = m["triage"]
    conf = m["confidence"]
    rc = _ring_color(conf)

    # ── الترويسة: صورة + اسم/ماركة/حجم + حلقة ثقة المطابقة ──
    brand_chip = (
        f'<span class="xc-brand">{_esc(m["brand"])}</span>' if m["brand"] else ""
    )
    meta_bits = []
    if m["size"]:
        unit = "" if any(u in m["size"] for u in ("مل", "ml", "ML")) else " مل"
        meta_bits.append(f'{_esc(m["size"])}{unit}')
    if m["concentration"]:
        meta_bits.append(_esc(m["concentration"]))
    meta = (f'<span class="xc-meta">{" · ".join(meta_bits)}</span>'
            if meta_bits else "")
    name_html = (
        f'<a class="xc-name" href="{_href(m["link"], m["name"])}" '
        f'target="_blank" rel="noopener">{_esc(m["name"])}</a>'
    )
    ring = (
        f'<div class="xc-ring" style="--rc:{rc};--rp:{conf:.0f}">'
        f'<div class="xc-ring-v"><b class="xc-num">{conf:.0f}</b>'
        '<small>تطابق</small></div></div>'
    )
    header = (
        '<div class="xc-head">'
        f'<div class="xc-img">{_img(m["image"], 92, border=tr["color"])}</div>'
        '<div class="xc-htxt">'
        f'{name_html}'
        f'<div class="xc-tags">{brand_chip}{meta}</div></div>'
        f'{ring}</div>'
    )

    # ── المؤشرات الأربعة ──
    # سعرنا (محايد) | متوسط السوق (رمادي) | الأرضية الموثوقة (أحمر إن أقل بكثير) | المقترح (أخضر).
    cheap = (
        m["trusted_low"] > 0 and m["our_price"] > 0
        and m["trusted_low"] < m["our_price"] * 0.9
    )
    floor_color = _RED if cheap else _NAME
    floor_sub = ("أرخص بكثير منّا" if cheap else "بعد التدقيق")
    raw_note = (
        f'خام {_money(m["raw_low"])}' if m["raw_low"] and m["raw_low"] != m["trusted_low"]
        else floor_sub
    )
    if m["margin_pct"] is not None:
        sug_sub = f'هامش {m["margin_pct"]:.0f}%'
        sug_sub_col = _MINT if m["margin_ok"] else _RED
    elif m["our_price"] > 0 and m["suggested"] > 0:
        d = m["our_price"] - m["suggested"]
        sug_sub = ("بلا تغيير" if abs(d) < 1 else
                   (f"▼ {_money(d)}" if d > 0 else f"▲ {_money(-d)}"))
        sug_sub_col = _MINT
    else:
        sug_sub, sug_sub_col = "", _MINT

    has_mkt = m["trusted_low"] > 0
    mv = (lambda v: _money(v) if v > 0 else "—")
    kpis = (
        '<div class="xc-kpis">'
        + _kpi_tile("سعرنا الحالي", _money(m["our_price"]),
                    color=_OURS, sub="المرجع", sub_color=_MUTED)
        + _kpi_tile("متوسط السوق", mv(m["market_avg"]), color=_MUTED,
                    sub=(f'تشتّت {_money(m["spread"])}' if has_mkt else "لا سوق"),
                    sub_color=_MUTED)
        + _kpi_tile("أقل سعر موثوق", mv(m["trusted_low"]), color=floor_color,
                    sub=(raw_note if has_mkt else "—"),
                    sub_color=(_RED if cheap else _MUTED))
        + _kpi_tile("المقترح الاستراتيجي", _money(m["suggested"]), color=_GREEN,
                    sub=sug_sub, sub_color=sug_sub_col, hero=True, icon="🎯")
        + "</div>"
    )

    ribbon = (
        '<div class="xc-ribbon">'
        '<div class="xc-live"><span class="xc-live-dot"></span>'
        'LIVE · تيرمنال التسعير</div>'
        f'<div class="xc-triage" style="--tc:{tr["color"]}" '
        f'title="{_esc(tr["reason"])}">{tr["label"]}</div>'
        "</div>"
    )

    card = (
        f'<div class="xc-card xc-{tr["key"]}" style="--xc-ac:{tr["color"]}">'
        f"{ribbon}{header}{kpis}{_ladder(m)}{_radar(m)}{_verdict_panel(m)}"
        "</div>"
    )
    # حارس الأسطر الفارغة (يمنع تسرّب HTML كنص — درس styles._no_blank_lines).
    return "".join(line for line in card.split("\n") if line.strip())


# ════════════════════════════════════════════════════════════════════
#  الأنماط (CSS مضمّن — يُحقن مرّة واحدة لكل تشغيل صفحة)
# ════════════════════════════════════════════════════════════════════
def expert_card_css() -> str:
    """أنماط التيرمنال: خلفية متدرّجة، زجاجية، أرقام Mono، توهّج، وتجاوب."""
    return """<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&family=Roboto+Mono:wght@500;700&display=swap');
.xc-card,.xc-card *{box-sizing:border-box;font-family:'Tajawal',sans-serif}
.xc-num{font-family:'Roboto Mono','Tajawal',monospace;font-variant-numeric:tabular-nums;
  letter-spacing:-.01em}
.xc-card{position:relative;direction:rtl;color:#E6EDF3;margin:10px 0;
  border-radius:18px;overflow:hidden;
  background:
    radial-gradient(120% 80% at 85% -10%,rgba(129,140,248,.12),transparent 55%),
    linear-gradient(180deg,#0d1320 0%,#0a0e17 100%);
  border:1px solid rgba(255,255,255,.07);
  border-top:3px solid var(--xc-ac,#818CF8);
  box-shadow:0 1px 0 rgba(255,255,255,.04) inset,0 18px 40px -20px rgba(0,0,0,.8);
  transition:transform .18s ease,box-shadow .18s ease}
.xc-card:hover{transform:translateY(-2px);
  box-shadow:0 1px 0 rgba(255,255,255,.06) inset,0 26px 54px -22px rgba(0,0,0,.85)}

/* ── شريط الحالة ── */
.xc-ribbon{display:flex;align-items:center;justify-content:space-between;
  padding:8px 14px;border-bottom:1px solid rgba(255,255,255,.05);
  background:rgba(255,255,255,.015)}
.xc-live{display:flex;align-items:center;gap:7px;font-size:.7rem;font-weight:700;
  letter-spacing:.06em;color:#94A3B8}
.xc-live-dot{width:8px;height:8px;border-radius:50%;background:#34D399;
  box-shadow:0 0 0 0 rgba(52,211,153,.7);animation:xcPulse 1.7s infinite}
@keyframes xcPulse{0%{box-shadow:0 0 0 0 rgba(52,211,153,.6)}
  70%{box-shadow:0 0 0 7px rgba(52,211,153,0)}100%{box-shadow:0 0 0 0 rgba(52,211,153,0)}}
.xc-triage{font-size:.8rem;font-weight:800;padding:4px 13px;border-radius:20px;
  color:var(--tc);background:color-mix(in srgb,var(--tc) 14%,transparent);
  border:1px solid color-mix(in srgb,var(--tc) 45%,transparent);cursor:help;
  white-space:nowrap}

/* ── الترويسة ── */
.xc-head{display:flex;align-items:center;gap:14px;padding:14px 14px 6px}
.xc-htxt{flex:1;min-width:0;display:flex;flex-direction:column;gap:7px}
.xc-name{font-size:1.18rem;font-weight:900;line-height:1.3;color:#F1F5F9;
  text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden}
.xc-name:hover{color:#fff}
.xc-tags{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.xc-brand{font-size:.74rem;font-weight:800;color:#0a0e17;padding:2px 11px;
  border-radius:7px;background:linear-gradient(135deg,#818CF8,#A5B4FC);
  box-shadow:0 2px 8px rgba(129,140,248,.35)}
.xc-meta{font-size:.8rem;color:#8B98A9}
.xc-ring{width:62px;height:62px;border-radius:50%;flex-shrink:0;position:relative;
  display:flex;align-items:center;justify-content:center;
  background:conic-gradient(var(--rc) calc(var(--rp)*1%),#1b2433 0)}
.xc-ring::before{content:"";position:absolute;inset:5px;border-radius:50%;
  background:#0b1018}
.xc-ring-v{position:relative;text-align:center;line-height:1}
.xc-ring-v b{font-size:1.12rem;font-weight:800;color:#F1F5F9}
.xc-ring-v small{display:block;font-size:.52rem;color:#8B98A9;margin-top:1px}

/* ── المؤشرات الأربعة ── */
.xc-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:10px 14px}
.xc-kpi{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);
  border-radius:12px;padding:9px 11px;min-width:0;
  border-top:2px solid color-mix(in srgb,var(--k) 70%,transparent)}
.xc-kpi-l{font-size:.7rem;color:#8B98A9;font-weight:500;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:4px}
.xc-kpi-ic{font-size:.82rem}
.xc-kpi-v{font-size:1.5rem;font-weight:700;color:var(--k);line-height:1.15;margin-top:2px}
.xc-kpi-v small{font-size:.56rem;color:#8B98A9;margin-inline-start:3px;font-weight:500}
.xc-kpi-d{font-size:.68rem;font-weight:700;margin-top:1px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.xc-kpi-hero{background:
    radial-gradient(120% 120% at 50% 0,rgba(34,197,94,.16),transparent 70%),
    rgba(34,197,94,.05);
  border-color:rgba(52,211,153,.4);
  box-shadow:0 0 22px -6px rgba(52,211,153,.5),0 0 0 1px rgba(52,211,153,.15) inset}
.xc-kpi-hero .xc-kpi-v{color:#34D399;text-shadow:0 0 18px rgba(52,211,153,.45)}

/* ── سُلّم عمق السوق ── */
.xc-ladder{padding:8px 16px 4px}
.xc-ladder-h{display:flex;justify-content:space-between;align-items:baseline;
  font-size:.74rem;font-weight:700;color:#A8B3C2;margin-bottom:16px}
.xc-spread{font-size:.66rem;color:#64748B;font-weight:500}
.xc-track{position:relative;height:8px;border-radius:6px;direction:ltr;
  background:linear-gradient(90deg,#16361f 0%,#3b3415 48%,#3a1d1d 100%);
  border:1px solid rgba(255,255,255,.07);margin:30px 0}
.xc-loss{position:absolute;top:0;bottom:0;left:0;border-radius:6px 0 0 6px;
  background:repeating-linear-gradient(45deg,rgba(239,68,68,.32),
    rgba(239,68,68,.32) 4px,transparent 4px,transparent 8px)}
.xc-floor-line{position:absolute;top:-5px;bottom:-5px;width:2px;
  background:#EF4444;box-shadow:0 0 8px rgba(239,68,68,.6)}
.xc-dot{position:absolute;top:50%;width:11px;height:11px;border-radius:50%;
  background:#F59E0B;transform:translate(-50%,-50%);
  border:2px solid #0a0e17;box-shadow:0 0 6px rgba(245,158,11,.6);z-index:2}
.xc-dot-ex{background:transparent;border:2px dashed #64748B;box-shadow:none;
  opacity:.6}
.xc-dot-crown{position:absolute;top:-19px;left:50%;transform:translateX(-50%);
  font-size:.72rem}
.xc-mk{position:absolute;top:50%;width:2px;height:20px;
  transform:translate(-50%,-50%);z-index:3}
.xc-mk-our{background:#818CF8;box-shadow:0 0 8px rgba(129,140,248,.7)}
.xc-mk-sug{background:#34D399;box-shadow:0 0 10px rgba(52,211,153,.8);height:24px}
.xc-mk-lbl{position:absolute;left:50%;transform:translateX(-50%);
  font-size:.6rem;font-weight:800;white-space:nowrap;padding:1px 5px;border-radius:5px;
  font-family:'Tajawal',sans-serif;direction:rtl}
.xc-mk-our .xc-mk-lbl{bottom:22px;color:#C7D2FE;background:rgba(129,140,248,.16)}
.xc-mk-sug .xc-mk-lbl{top:26px;color:#34D399;background:rgba(52,211,153,.16)}
.xc-axis{display:flex;justify-content:space-between;font-size:.62rem;color:#64748B;
  direction:ltr;margin-top:2px}

/* ── رادار المنافسين (قائمة صفوف غنية) ── */
.xc-radar{padding:6px 14px 10px}
.xc-radar-h{font-size:.74rem;font-weight:700;color:#A8B3C2;margin-bottom:7px}
.xc-radar-h small{color:#64748B;font-weight:500;margin-inline-start:4px}
.xc-radar-list{display:flex;flex-direction:column;gap:6px}
.xc-row{display:flex;align-items:center;gap:10px;padding:7px 9px;
  background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);
  border-radius:11px;text-decoration:none;
  transition:border-color .15s,background .15s}
.xc-row:hover{border-color:rgba(245,158,11,.45);background:rgba(245,158,11,.05)}
.xc-row-img{flex-shrink:0}
.xc-row-mid{flex:1;min-width:0}
.xc-row-nm{display:flex;align-items:baseline;gap:5px}
.xc-row-crown{flex-shrink:0;font-size:.78rem}
.xc-row-nmt{font-size:.9rem;font-weight:700;color:#E6EDF3;line-height:1.35;
  word-break:break-word}
.xc-row-sz{color:#8B98A9;font-weight:400;font-size:.82rem}
.xc-row-meta{font-size:.72rem;color:#8B98A9;margin-top:3px;
  display:flex;gap:9px;flex-wrap:wrap}
.xc-row-pr{text-align:center;flex-shrink:0;min-width:80px}
.xc-row-price{font-size:1.08rem;font-weight:700;color:#F59E0B;line-height:1.1}
.xc-row-price small{font-size:.54rem;color:#8B98A9;margin-inline-start:2px;
  font-weight:500}
.xc-row-d{font-size:.7rem;font-weight:700;margin-top:1px}
.xc-row-ex{opacity:.6}
.xc-row-ex .xc-row-nmt,.xc-row-ex .xc-row-price{
  text-decoration:line-through;color:#64748B}
.xc-empty{font-size:.8rem;color:#64748B;padding:6px 0}

/* ── حكم السرب ── */
.xc-verdict{display:flex;align-items:stretch;gap:0;margin:2px 14px 14px;
  padding:10px 12px;border-radius:13px;
  background:linear-gradient(180deg,rgba(212,175,55,.07),rgba(212,175,55,.02));
  border:1px solid rgba(212,175,55,.32);
  box-shadow:0 0 18px -6px rgba(212,175,55,.4)}
.xc-vd-agent{flex:1;display:flex;align-items:center;gap:8px;min-width:0;padding:0 6px}
.xc-vd-ic{font-size:1.1rem;flex-shrink:0}
.xc-vd-tx{min-width:0;display:flex;flex-direction:column;line-height:1.25}
.xc-vd-tx b{font-size:.7rem;color:#D4AF37;font-weight:800}
.xc-vd-tx span{font-size:.74rem;color:#CBD5E1;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.xc-vd-sep{width:1px;background:rgba(212,175,55,.22);flex-shrink:0}

/* ── تجاوب الهواتف ── */
@media (max-width:560px){
  .xc-kpis{grid-template-columns:repeat(2,1fr)}
  .xc-kpi-v{font-size:1.3rem}
  .xc-verdict{flex-direction:column;gap:8px}
  .xc-vd-sep{width:100%;height:1px}
  .xc-vd-tx span{white-space:normal}
  .xc-name{font-size:1.05rem}
}
</style>
"""


# ════════════════════════════════════════════════════════════════════
#  الغلاف الرفيع (يستورد Streamlit بكسل) + أزرار الإجراء
# ════════════════════════════════════════════════════════════════════
def _key_for(data: Mapping[str, Any]) -> str:
    """مفتاح أزرار مستقرّ من اسم/معرّف المنتج (آمن لـ Streamlit keys)."""
    raw = _clean(_get(data, "product", "sku")) or _clean(_get(data, "product", "name"))
    return "".join(ch if ch.isalnum() else "_" for ch in raw)[:48] or "xc"


def render_expert_card(
    data: Mapping[str, Any],
    *,
    inject_css: bool = True,
    show_actions: bool = True,
    key: Optional[str] = None,
) -> Optional[str]:
    """يعرض بطاقة الخبير الكاملة ويُرجع الإجراء المختار ("approve"/"edit"/"archive") أو None.

    الأنماط تُحقن مرّة واحدة لكل تشغيل صفحة. الأزرار تُرسم بـ Streamlit (لا HTML)
    كي تكون فعّالة — زرّ الاعتماد Primary يحمل السعر المقترح المحسوب.
    """
    import streamlit as st  # استيراد كسول → الاختبارات تبقى headless

    if inject_css and not st.session_state.get("_mhw_expert_css"):
        st.markdown(expert_card_css(), unsafe_allow_html=True)
        st.session_state["_mhw_expert_css"] = True

    st.markdown(build_expert_card_html(data), unsafe_allow_html=True)

    if not show_actions:
        return None

    k = key or _key_for(data)
    suggested = analyze(data)["suggested"]
    c1, c2, c3 = st.columns([1.7, 1, 1])
    chosen: Optional[str] = None
    if c1.button(f"✅ اعتماد المقترح ({_money(suggested)} ر.س)",
                 key=f"xc_ok_{k}", type="primary", use_container_width=True):
        chosen = "approve"
    if c2.button("✏️ تعديل يدوي", key=f"xc_edit_{k}", use_container_width=True):
        chosen = "edit"
    if c3.button("📥 أرشفة / رفض", key=f"xc_arch_{k}", use_container_width=True):
        chosen = "archive"
    return chosen


# ════════════════════════════════════════════════════════════════════
#  محوّل البيانات الحقيقية: صف نتيجة المحرّك → مدخلات بطاقة الخبير
# ════════════════════════════════════════════════════════════════════
def row_to_expert(row: Any) -> dict[str, Any]:
    """يحوّل صفاً حقيقياً (أعمدة المحرّك العربية) إلى مدخلات بطاقة الخبير.

    #PRESERVED_LOGIC: يعيد استخدام محلّلات ``product_card`` المُختبَرة (``build_card``
    + ``parse_competitor_details`` + ``our_page_url``) فيرث معالجة كل أسماء الأعمدة
    ومساري التسعير/المفقودات وروابط المنتجات — دون إعادة اختراع ودون اختلاق بيانات.

    السعر المقترح/الثقة/السبب تُقرأ من الصف إن وُجدت (يملؤها الذكاء لاحقاً في
    Batch 2)، وإلا يشتقّها العقل ``analyze`` حتمياً فتبقى البطاقة كاملة دائماً.
    """
    from conf.constants import COL_SUGGESTED_PRICE
    from ui.components.product_card import (  # استيراد كسول (محلّلات خالصة)
        build_card, our_page_url, parse_competitor_details,
    )

    card = build_card(row)
    comps = parse_competitor_details(row)  # مرتّبون بالسعر، يحملون score/size

    def cell(*names: str) -> Any:
        """أول قيمة غير فارغة من أعمدة بديلة (يتجاهل None/''/nan)."""
        for n in names:
            v = row.get(n) if hasattr(row, "get") else None
            if v not in (None, "") and str(v).lower() != "nan":
                return v
        return None

    data: dict[str, Any] = {
        "product": {
            "name": card.our_name, "brand": card.our_brand,
            "size": card.our_size, "concentration": card.our_type,
            "image": card.our_image, "link": our_page_url(card),
            "sku": card.our_sku,
        },
        "our_price": card.our_price,
        "cost": _f(cell("التكلفة", "سعر_التكلفة", "cost")),
        "competitors": comps,
    }

    # سعر مقترح من المحرّك/الذكاء إن قُرّر — لا نعيد حسابه (analyze يحترمه).
    suggested = _f(cell(COL_SUGGESTED_PRICE))
    if suggested > 0:
        data["suggested_price"] = suggested

    margin = _f(cell("هامش_أدنى_نسبة", "min_margin_pct"))
    if margin > 0:
        data["min_margin_pct"] = margin

    # ملاحظات السرب (Batch 2 يملؤها؛ هنا قراءة فقط إن توفّرت أعمدة AI).
    swarm: dict[str, Any] = {}
    conf = _f(cell("ثقة_AI", "نسبة_التطابق"))
    if conf > 0:
        swarm["identifier"] = {"confidence": conf}
    reason = _clean(cell("السبب"))
    if reason:
        swarm["strategist"] = {"note": reason}
    if swarm:
        data["swarm"] = swarm

    return data


def render_row_expert(
    row: Any, *, inject_css: bool = True, show_actions: bool = True,
    key: Optional[str] = None,
) -> Optional[str]:
    """يعرض بطاقة الخبير من صف نتيجة حقيقي (غلاف رفيع فوق المحوّل + ``render_expert_card``)."""
    return render_expert_card(
        row_to_expert(row), inject_css=inject_css,
        show_actions=show_actions, key=key,
    )


# ════════════════════════════════════════════════════════════════════
#  بيانات وهمية واقعية (يشاركها العرض التجريبي والاختبارات)
# ════════════════════════════════════════════════════════════════════
def demo_data() -> list[dict[str, Any]]:
    """أربع حالات تغطّي: 🟢 جاهز · 🟡 هبوط حادّ · 🔴 تستر يلوّث السوق · ⚪ بلا منافسين."""
    return [
        {   # 🟢 منطقة خضراء: تطابق عالٍ، هامش آمن، لا هبوط حادّ
            "product": {
                "name": "Lattafa Khamrah Eau de Parfum", "brand": "لطافة",
                "size": 100, "concentration": "EDP", "sku": "20988",
                "link": "https://mahwous.com",
                "image": "https://images.unsplash.com/photo-1615634260167-c8cdede054de?w=400",
            },
            "our_price": 135, "cost": 78, "min_margin_pct": 15,
            "competitors": [
                {"store": "نايس ون", "name": "لطافة خمرة او دو بارفان للجنسين",
                 "size": 100, "price": 149, "score": 98, "authority": 1.0,
                 "link": "https://niceonesa.com",
                 "image": "https://images.unsplash.com/photo-1594035910387-fea47794261f?w=200"},
                {"store": "أمانة", "name": "Lattafa Khamrah Eau de Parfum",
                 "size": 100, "price": 142, "score": 95, "authority": 0.7,
                 "image": "https://images.unsplash.com/photo-1615634260167-c8cdede054de?w=200"},
                {"store": "متجر العنبر", "name": "لطافة خمرة الأصلية",
                 "size": 100, "price": 138, "score": 96, "authority": 0.4,
                 "image": "https://images.unsplash.com/photo-1541643600914-78b084683601?w=200"},
            ],
        },
        {   # 🟡 منطقة صفراء: هبوط سعري حادّ (المنافسون أرخص بكثير)
            "product": {
                "name": "Dior Sauvage Eau de Parfum", "brand": "Dior",
                "size": 100, "concentration": "EDP", "sku": "10421",
                "link": "https://mahwous.com",
                "image": "https://images.unsplash.com/photo-1592945403244-b3fbafd7f539?w=400",
            },
            "our_price": 545, "cost": 360, "min_margin_pct": 12,
            "competitors": [
                {"store": "نايس ون", "name": "ديور سوفاج او دو بارفان للرجال",
                 "size": 100, "price": 489, "score": 97, "authority": 1.0,
                 "link": "https://niceonesa.com",
                 "image": "https://images.unsplash.com/photo-1592945403244-b3fbafd7f539?w=200"},
                {"store": "روائح الخليج", "name": "Dior Sauvage Eau de Parfum",
                 "size": 100, "price": 462, "score": 93, "authority": 0.5,
                 "image": "https://images.unsplash.com/photo-1588405748880-12d1d2a59f75?w=200"},
                {"store": "بيت العطور", "name": "ديور سوفاج للرجال الأصلي",
                 "size": 100, "price": 455, "score": 90, "authority": 0.3,
                 "image": "https://images.unsplash.com/photo-1541643600914-78b084683601?w=200"},
            ],
        },
        {   # 🔴/🟡 المدقّق يستبعد تستر يلوّث المتوسط (سعر شاذ)
            "product": {
                "name": "Chanel Bleu de Chanel Parfum", "brand": "Chanel",
                "size": 100, "concentration": "Parfum", "sku": "30155",
                "link": "https://mahwous.com",
                "image": "https://images.unsplash.com/photo-1588405748880-12d1d2a59f75?w=400",
            },
            "our_price": 720, "cost": 520, "min_margin_pct": 15,
            "competitors": [
                {"store": "نايس ون", "name": "شانيل بلو دو شانيل بارفان للرجال",
                 "size": 100, "price": 689, "score": 96, "authority": 1.0,
                 "link": "https://niceonesa.com",
                 "image": "https://images.unsplash.com/photo-1588405748880-12d1d2a59f75?w=200"},
                {"store": "متجر الفخامة", "name": "Bleu de Chanel Parfum",
                 "size": 100, "price": 705, "score": 94, "authority": 0.6,
                 "image": "https://images.unsplash.com/photo-1592945403244-b3fbafd7f539?w=200"},
                {"store": "عروض السوق", "name": "بلو دي شانيل بارفان (تستر بلا علبة)",
                 "size": "100 (تستر)", "price": 240, "score": 88, "authority": 0.2,
                 "is_tester": True, "exclude_reason": "تستر بلا علبة — لا يُقارَن",
                 "image": "https://images.unsplash.com/photo-1615634260167-c8cdede054de?w=200"},
                {"store": "ركن العطور", "name": "شانيل بلو دو شانيل 100 مل",
                 "size": 100, "price": 698, "score": 92, "authority": 0.5,
                 "image": "https://images.unsplash.com/photo-1594035910387-fea47794261f?w=200"},
            ],
        },
        {   # ⚪ بلا منافسين — منتج منفرد
            "product": {
                "name": "Mahwous Private Oud Intense", "brand": "مهووس",
                "size": 50, "concentration": "Extrait", "sku": "90001",
                "link": "https://mahwous.com", "image": "",
            },
            "our_price": 410, "cost": 230, "min_margin_pct": 20,
            "competitors": [],
        },
    ]
