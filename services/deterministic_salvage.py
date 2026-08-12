"""services/deterministic_salvage.py — إنقاذ المستبعدات حتمياً (بلا ذكاء/بلا API).

الخلفية
───────
محرّك المطابقة (``engines/engine.py``) يُلزم الماركة إلزاماً: عطرٌ بلا ماركة
معروفة — أو ماركته مُفهرَسة باسم مختلف بين طرفينا والمنافس — يُستبعد قبل أي
مقارنة اسم (``engine.py:1890`` ``if not _our_br_norm: return []``). النتيجة:
مئات العطور الحقيقية تُستبعد رغم وجود نظير مطابق تماماً عند منافس.

هذه الخدمة **لا تلمس المحرّك** (``#PRESERVED_LOGIC``). بدلاً من ذلك تمرّ على
المستبعدات مروراً واحداً، وتبحث لكل عطرٍ مفرد عن أقوى نظير في **كامل** كتالوج
المنافسين **بلا حجب ماركة**، ثم تنقل المطابقات القوية إلى «⚠️ تحت المراجعة»
فقط — لا تسعير تلقائي، قابلة للتراجع الكامل (نقل صفوف بين الأقسام).

القواعد الحاكمة (متطابقة مع ``ai_router_service``)
──────────────────────────────────────────────
1. خدمة نقية: لا استيراد Streamlit.
2. لا فقدان صامت: كل صف يظهر في «مُنقَذ» أو «باقٍ مستبعد» — التحقق الحسابي إلزامي.
3. الدقّة أولى: عتبة مزدوجة صارمة (token_set + token_sort) + حارس الجنس، كي لا
   نُغرق المراجعة بمطابقات كاذبة على متجر حيّ.
4. المخرجات إلى المراجعة حصراً (لا رفع/خفض/موافق) — قرار السعر للمالك.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from conf.constants import (
    COL_BRAND,
    COL_COMP_COUNT,
    COL_COMP_IMAGE,
    COL_COMP_LINK,
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_COMP_STORE,
    COL_DECISION,
    COL_DIFF,
    COL_GENDER,
    COL_MATCH_RATIO,
    COL_OUR_NAME,
    COL_OUR_PRICE,
)
from engines.engine import extract_gender, normalize_name

logger = logging.getLogger(__name__)

# عمود قائمة المنافسين بمفاتيح إنجليزية (يقرأه parse_competitor_details لبطاقة الحَلَبة)
_COL_ALL_COMP = "جميع_المنافسين"
_COL_SOURCE = "مصدر_المطابقة"
_DECISION_TEXT = "⚠️ تحت المراجعة (إنقاذ حتمي)"
_SOURCE_TAG = "rescue_deterministic"

# عتبات الإنقاذ الافتراضية — صارمة عمداً (الدقّة أولى على متجر حيّ)
DEFAULT_SET_MIN = 90   # token_set_ratio: يتطلب تطابق مجموعة الكلمات
DEFAULT_SORT_MIN = 82  # token_sort_ratio: يعاقب اختلاف الطول/الكلمات الزائدة → يقتل الإيجابيات الجزئية

# ── مصنِّف الطبيعة: نُنقذ العطور المفردة فقط (لا تجميل/تستر/طقم/عيّنة) ──
_NONPERF = re.compile(
    r"مزيل|ديودر|deodorant|لوشن|lotion|شامبو|shampoo|بلسم|صابون|soap|كريم|cream|"
    r"سيروم|serum|روج|شفاه|lip|اساس|فاونديشن|foundation|كونسيلر|concealer|كونتور|"
    r"contour|باودر|بودر|powder|بلاشر|blush|احمر خدود|خدود|مسكرا|mascara|لاينر|"
    r"eyeliner|ظلال|هايلايتر|highlight|بريمر|primer|قناع|ماسك|mask|مقشر|scrub|غسول|"
    r"تونر|toner|واقي شمس|sunscreen|spf|منشف|قفاز|جهاز|ديفيوزر|شمع|candle|مكواة|"
    r"سشوار|صبغ|اظافر|nail|حواجب|كحل|ريتينول|هيالورونيك|بشره|بشرة|امبول|شعر\b|"
    r"زيت الشعر|معطر شعر|عطر الشعر|hair|بخاخ شعر|بخور|دهن عود|زيت عطر|زيت عطري|"
    r"مخلط|مبخر|عود تبخير",
    re.IGNORECASE,
)
_SET = re.compile(
    r"طقم|مجموع|collection|كولكشن|هدي|gift|بوكس\b|بندل|bundle|\bset\b|قطع\)|عطرين|\d\s*قطع",
    re.IGNORECASE,
)
_SAMPLE = re.compile(r"عين[ةه]\b|عينات|تقسيم|decant|sample|ديكانت|فيال|vial", re.IGNORECASE)
_TESTER = re.compile(r"تستر|تيستر|tester", re.IGNORECASE)


def is_single_perfume(name: str) -> bool:
    """هل الاسم عطرٌ مفرد قابل للإنقاذ؟ (يستبعد التجميل/التستر/الطقم/العيّنة)."""
    if not name:
        return False
    if _TESTER.search(name) or _SAMPLE.search(name) or _SET.search(name):
        return False
    if _NONPERF.search(name):
        return False
    return True


@dataclass
class DetSalvageResult:
    """نتيجة الإنقاذ الحتمي.

    - salvaged_matches : مستبعدات وُجد لها نظير قوي → تنتقل لـ «⚠️ مراجعة»
                         (بحقول المنافس مملوءة، جاهزة للعرض في بطاقة الحَلَبة).
    - stayed_excluded  : كل ما بقي مستبعداً (غير عطر، أو لم يبلغ العتبة).
    - total_rows       : إجمالي صفوف الإدخال (للتحقق الحسابي).
    - n_perfumes       : عدد العطور المفردة التي فُحصت فعلاً.
    - errors           : أخطاء/ملاحظات.
    """

    salvaged_matches: pd.DataFrame = field(default_factory=pd.DataFrame)
    stayed_excluded: pd.DataFrame = field(default_factory=pd.DataFrame)
    total_rows: int = 0
    n_perfumes: int = 0
    errors: list[str] = field(default_factory=list)


def _f(value: Any) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _clean(value: Any) -> str:
    s = "" if value is None else str(value).strip()
    return "" if s.lower() in ("nan", "none", "<na>") else s


def _fill_competitor_fields(row: dict[str, Any], comp: dict[str, Any], score: int) -> dict[str, Any]:
    """يملأ حقول المنافس في صف مُنقَذ (يعرضها بطاقة الحَلَبة + يوجّهه للمراجعة)."""
    our_price = _f(row.get(COL_OUR_PRICE))
    comp_price = _f(comp.get("price"))
    row[COL_COMP_NAME] = comp.get("name", "")
    row[COL_COMP_PRICE] = comp_price
    row[COL_COMP_STORE] = comp.get("competitor", "")
    row[COL_COMP_LINK] = comp.get("product_url", "")
    row[COL_COMP_IMAGE] = comp.get("image_url", "")
    row[COL_MATCH_RATIO] = score
    row[COL_DIFF] = round(our_price - comp_price, 2) if (our_price and comp_price) else 0.0
    row[COL_COMP_COUNT] = 1
    row[_COL_ALL_COMP] = json.dumps(
        [{
            "name": comp.get("name", ""),
            "price": comp_price,
            "competitor": comp.get("competitor", ""),
            "image_url": comp.get("image_url", ""),
            "product_url": comp.get("product_url", ""),
            "size": comp.get("size", ""),
            "score": score,
        }],
        ensure_ascii=False,
    )
    row[COL_DECISION] = _DECISION_TEXT
    row[_COL_SOURCE] = _SOURCE_TAG
    return row


def _best_matches(
    queries_norm: list[str],
    choices_norm: list[str],
    *,
    set_min: int,
    top_k: int = 5,
    chunk: int = 128,
) -> list[list[tuple[int, int]]]:
    """لكل استعلام: أفضل ``top_k`` مرشّحين بـ token_set ≥ ``set_min`` — [(idx, score), …].

    عبر cdist مُتّجه مُقطَّع (ذاكرة محدودة) + argpartition للأعلى-K (رخيص). أخذ
    عدّة مرشّحين — لا الأعلى فقط — يمنع فوات المطابقة الحقيقية حين يعلوها اسمٌ آخر
    عالي token_set لكنه يسقط في تأكيد token_sort. القائمة مرتّبة تنازلياً بالدرجة.
    """
    import numpy as np
    from rapidfuzz import fuzz
    from rapidfuzz.process import cdist

    out: list[list[tuple[int, int]]] = [[] for _ in queries_norm]
    if not choices_norm:
        return out
    k = min(top_k, len(choices_norm))
    for start in range(0, len(queries_norm), chunk):
        block = queries_norm[start:start + chunk]
        mat = cdist(block, choices_norm, scorer=fuzz.token_set_ratio,
                    dtype=np.uint8, workers=-1)
        # أعلى-k فهارس لكل صف (غير مرتّبة) ثم رتّبها تنازلياً
        part = np.argpartition(mat, -k, axis=1)[:, -k:]
        for i in range(len(block)):
            idxs = part[i]
            scored = sorted(
                ((int(j), int(mat[i, j])) for j in idxs),
                key=lambda t: -t[1],
            )
            out[start + i] = [(j, s) for j, s in scored if s >= set_min]
    return out


def salvage_excluded_deterministic(
    excluded_df: pd.DataFrame,
    competitors: pd.DataFrame,
    *,
    set_min: int = DEFAULT_SET_MIN,
    sort_min: int = DEFAULT_SORT_MIN,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> DetSalvageResult:
    """تُنقذ المستبعدات إلى «مراجعة» عبر بحث اسم حتمي بلا حجب ماركة.

    Args:
        excluded_df: صفوف قسم «مستبعد» (أعمدة المحرّك العربية).
        competitors: كتالوج المنافسين — أعمدة مطلوبة: ``product_name``, ``price``.
            اختياري: ``product_url``, ``image_url``, ``competitor``, ``size``, ``gender``.
        set_min: عتبة token_set_ratio الدنيا.
        sort_min: عتبة token_sort_ratio الدنيا (تأكيد صارم يقتل التطابق الجزئي).
        progress_cb: ردّ نداء تقدم ``(done, total)``.

    Returns:
        DetSalvageResult — لا فقدان (مُنقَذ + باقٍ = الإجمالي).
    """
    from rapidfuzz import fuzz

    errors: list[str] = []
    total = len(excluded_df)

    if excluded_df.empty:
        return DetSalvageResult(total_rows=0)

    if competitors is None or competitors.empty or "product_name" not in competitors.columns:
        errors.append("لا يوجد كتالوج منافسين صالح — بقيت كل المستبعدات كما هي")
        return DetSalvageResult(
            stayed_excluded=excluded_df.copy(), total_rows=total, errors=errors,
        )

    rows = excluded_df.to_dict("records")

    # ── فصل العطور المفردة (المرشّحة) عن الباقي (يبقى مستبعداً بلا فحص) ──
    cand_pos: list[int] = []   # مواضع الصفوف المرشّحة في rows
    for i, r in enumerate(rows):
        if is_single_perfume(_clean(r.get(COL_OUR_NAME))):
            cand_pos.append(i)

    if not cand_pos:
        return DetSalvageResult(
            stayed_excluded=excluded_df.copy(), total_rows=total,
            n_perfumes=0, errors=errors,
        )

    # ── تطبيع أسماء المنافسين مرّة واحدة بدالة المحرّك نفسها (تناظر عادل) ──
    comp_recs = competitors.to_dict("records")
    choices_norm: list[str] = []
    keep_recs: list[dict[str, Any]] = []
    for c in comp_recs:
        q = normalize_name(_clean(c.get("product_name")))
        if q:
            choices_norm.append(q)
            keep_recs.append(c)
    if not choices_norm:
        errors.append("تعذّر تطبيع أسماء المنافسين — بقيت كل المستبعدات كما هي")
        return DetSalvageResult(
            stayed_excluded=excluded_df.copy(), total_rows=total, errors=errors,
        )

    queries_norm = [normalize_name(_clean(rows[p].get(COL_OUR_NAME))) for p in cand_pos]
    best = _best_matches(queries_norm, choices_norm, set_min=set_min)

    salvaged_idx: set[int] = set()
    salvaged_records: list[dict[str, Any]] = []
    done = 0
    for k, pos in enumerate(cand_pos):
        done += 1
        if progress_cb is not None and (done % 64 == 0 or done == len(cand_pos)):
            try:
                progress_cb(done, len(cand_pos))
            except Exception:
                pass

        cands = best[k]
        if not cands:
            continue
        q = queries_norm[k]
        our_g = extract_gender(_clean(rows[pos].get(COL_OUR_NAME)))
        chosen: Optional[tuple[dict[str, Any], int]] = None
        for cidx, set_score in cands:
            cand_norm = choices_norm[cidx]
            # تأكيد صارم: token_sort يعاقب اختلاف الطول/الكلمات الزائدة
            if int(fuzz.token_sort_ratio(q, cand_norm)) < sort_min:
                continue
            # حارس الجنس: لا نطابق رجالياً بنسائي إذا كان الجنس صريحاً للطرفين
            comp = keep_recs[cidx]
            comp_g = extract_gender(_clean(comp.get("product_name"))) or _clean(comp.get("gender"))
            if our_g in ("رجالي", "نسائي") and comp_g in ("رجالي", "نسائي") and our_g != comp_g:
                continue
            chosen = (comp, set_score)
            break
        if chosen is None:
            continue
        comp, set_score = chosen

        row = dict(rows[pos])
        comp_norm_view = {
            "name": _clean(comp.get("product_name")),
            "price": comp.get("price"),
            "competitor": _clean(comp.get("competitor")),
            "product_url": _clean(comp.get("product_url")),
            "image_url": _clean(comp.get("image_url")),
            "size": _clean(comp.get("size")),
        }
        salvaged_records.append(_fill_competitor_fields(row, comp_norm_view, set_score))
        salvaged_idx.add(pos)

    salvaged_matches = (
        pd.DataFrame(salvaged_records) if salvaged_records else pd.DataFrame()
    )
    stayed_excluded = (
        excluded_df.iloc[[i for i in range(total) if i not in salvaged_idx]].copy()
    )

    # ── التحقق الحسابي: لا فقدان ──
    if len(salvaged_matches) + len(stayed_excluded) != total:
        raise AssertionError(
            f"خطأ حسابي في الإنقاذ الحتمي: "
            f"{len(salvaged_matches)} + {len(stayed_excluded)} ≠ {total}"
        )

    return DetSalvageResult(
        salvaged_matches=salvaged_matches,
        stayed_excluded=stayed_excluded,
        total_rows=total,
        n_perfumes=len(cand_pos),
        errors=errors,
    )


def load_perfume_competitor_pool(db_path: Optional[str] = None) -> pd.DataFrame:
    """يحمّل كتالوج منافسين عطرياً (قراءة فقط) من ``competitor_products_store``.

    يقصر النتيجة على الصفوف التي يبدو اسمها عطراً (تقليل الضجيج + تسريع cdist).
    الأعمدة: product_name, price, product_url, image_url, competitor, size, gender.
    """
    import sqlite3

    from conf.constants import COMPETITOR_DB_PATH

    path = db_path or str(COMPETITOR_DB_PATH)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT product_name, price, product_url, image_url, "
            "competitor, size, gender FROM competitor_products_store "
            "WHERE product_name IS NOT NULL AND product_name <> ''",
            con,
        )
    finally:
        con.close()
    # فلترة عطرية خفيفة: احتفظ بما يحمل مؤشّر عطر (تركيز/كلمة عطر) — يقلّل مطابقة عطرنا بمستحضر
    perf = df["product_name"].astype(str).str.contains(
        r"عطر|او\s*د[وي]|أو\s*د[وي]|بارفيوم|برفيوم|بارفان|برفان|تواليت|كولون|"
        r"اكستريت|elixir|اليكسير|\bedp\b|\bedt\b|\bedc\b|parfum|toilette|cologne",
        case=False, regex=True, na=False,
    )
    out = df[perf].reset_index(drop=True)
    return out if not out.empty else df


def apply_deterministic_salvage(
    sections: dict[str, pd.DataFrame],
    *,
    result: Any = None,
    pool: Optional[pd.DataFrame] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> int:
    """يشغّل الإنقاذ الحتمي ويطبّقه على ``sections`` (تعديل موضعي) — نواة مشتركة.

    ينقل المطابقات القوية من «excluded» إلى «review»، يستبدل «excluded» بالباقي،
    ويحدّث ``result.section_counts`` إن مُرِّر (كي لا يتجمّد عدّاد لوحة التحكم).
    خدمة نقية: لا Streamlit، لا كتابة قرص — المستدعي يتكفّل بالحفظ.

    Returns: عدد الصفوف المُنقَذة (0 إن لا مستبعدات أو لا نظير قوي).
    """
    excluded_df = sections.get("excluded", pd.DataFrame())
    if excluded_df is None or excluded_df.empty:
        return 0

    if pool is None:
        pool = load_perfume_competitor_pool()
    if pool is None or pool.empty:
        return 0

    res = salvage_excluded_deterministic(excluded_df, pool, progress_cb=progress_cb)
    n_match = len(res.salvaged_matches)
    if n_match == 0:
        return 0

    old_review = sections.get("review", pd.DataFrame())
    sections["review"] = pd.concat(
        [old_review, res.salvaged_matches], ignore_index=True,
    )
    sections["excluded"] = res.stayed_excluded

    # تحديث العدّاد المخزَّن (لقطة لوحة التحكم) من الأقسام الحيّة بعد النقل
    if result is not None and hasattr(result, "section_counts"):
        try:
            from core.enums import SectionType
            result.section_counts[SectionType.EXCLUDED] = len(sections["excluded"])
            result.section_counts[SectionType.REVIEW] = len(sections["review"])
        except Exception:  # noqa: BLE001 — العدّاد تجميلي؛ فشله لا يُبطل النقل
            pass
    return n_match
