"""services/ownership_matcher.py — كاشف «نملكه باسم آخر» (دقّة قسم المفقودات).

المشكلة: يظهر في «المفقودات» منتجٌ نملكه فعلاً لكن مكتوب باسم مختلف
(إملاء/ترجمة حرفية مختلفة: كنزو/كينزو، او دو/او دي، تستر/عطر). الحاجز القديم
``engine.smart_missing_barrier`` (token_set>=92) كان يخلط الحجم والنكهة والماركة
فيلتقط 1,334 معظمها **خاطئ** (30مل ضد 100مل، كول ووتر ضد كول ووتر ويف) —
لذلك لم يُوصَل قط.

الطريقة الصحيحة هنا: منتجٌ «نملكه باسم آخر» ⇔ يتطابق مع صفٍّ في كتالوجنا على
**أربع بصمات صارمة معاً**:
  1. الحجم (extract_size) — 30مل ≠ 100مل.
  2. التركيز (extract_type) — EDP ≠ EDT ≠ PARFUM (قاعدة SKU: تركيز مختلف = منتج مختلف).
  3. الجنس (extract_gender) — رجالي ≠ نسائي (لا يُطابَق المتعارضان).
  4. الكلمات المميّزة (normalize_name، بعد حذف الضجيج/الحجم/التركيز): **مجموعتان
     متساويتان تماماً** — نفس العدد ومطابقة أحادية لكل كلمة فوق عتبة إملائية —
     فأي كلمة زائدة (ويف/انليميتد/نايت) تعني منتجاً مختلفاً فلا يُطابَق.

كل الدوال **نقية وقراءة-فقط**: لا تغيّر بيانات، وأي خطأ ⇒ نتيجة فارغة (fail-open)
فلا تكسر صفحة المفقودات. تُعيد الاستخدام حصراً لمستخرجات engine.py المُثبتة
(#PRESERVED_LOGIC) — لا تطبيع جديد ولا لمس للمحرك.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from engines.engine import (
    normalize_name,
    extract_size,
    extract_type,
    extract_gender,
    _our_product_names_series,
)
from rapidfuzz import fuzz

# عتبة تطابق الكلمة الواحدة (تسامح إملائي: كنزو≈كينزو، جولد≈قولد).
TOKEN_THRESHOLD = 86
# اسم منتج المنافس في صفوف المفقودات (نفس مفتاح الإرسال).
COMP_NAME_COL = "منتج_المنافس"


@dataclass(frozen=True)
class OwnedMatch:
    """مطابقة واحدة: منتجٌ في المفقودات نملكه في كتالوجنا باسم مكتوب مختلف."""
    df_index: Any        # فهرس الصف في missing_df (للتعليم/الإخفاء لاحقاً)
    comp_name: str       # الاسم كما يظهر في المفقودات
    our_name: str        # الاسم كما هو في كتالوجنا
    size_ml: int         # الحجم المشترك (مل)
    conc: str            # التركيز المشترك (EDP/EDT/…)


def _core_tokens(name: str) -> list[str]:
    """الكلمات المميّزة (بعد حذف الضجيج/الحجم/التركيز) بطول ≥2. خالص."""
    return [w for w in normalize_name(name).split() if len(w) >= 2]


def _size_key(size_ml: float) -> int:
    """مفتاح الحجم الصحيح (0 = غير معروف). خالص."""
    return int(round(size_ml)) if size_ml else 0


def _core_equivalent(a: list[str], b: list[str], thr: int = TOKEN_THRESHOLD) -> bool:
    """هل مجموعتا الكلمات المميّزة متساويتان تماماً؟ خالص.

    شرطان: نفس العدد (فلا كلمة زائدة تفرّق منتجاً عن آخر) + مطابقة أحادية لكل
    كلمة في ``a`` بكلمة مختلفة في ``b`` بدرجة ≥ العتبة (تسامح إملائي فقط).
    """
    if not a or len(a) != len(b):
        return False
    remaining = list(b)
    for ta in a:
        best_i, best_sc = -1, -1
        for i, tb in enumerate(remaining):
            sc = fuzz.ratio(ta, tb)
            if sc > best_sc:
                best_sc, best_i = sc, i
        if best_sc < thr:
            return False
        remaining.pop(best_i)
    return True


def _gender_compatible(g1: str, g2: str) -> bool:
    """متوافقان إلا إذا كانا معروفَين ومتعارضَين (رجالي ضد نسائي). خالص."""
    return not (g1 and g2 and g1 != g2)


def _build_catalog_index(our_names: list[str]) -> dict:
    """فهرس الكتالوج bucket=(size_key, conc) → [(core, gender, raw)]. خالص."""
    idx: dict = defaultdict(list)
    for nm in our_names:
        nm = str(nm)
        idx[(_size_key(extract_size(nm)), extract_type(nm))].append(
            (_core_tokens(nm), extract_gender(nm), nm)
        )
    return idx


def find_owned_matches(
    missing_df: pd.DataFrame,
    our_df: Any,
    *,
    name_col: str = COMP_NAME_COL,
) -> list[OwnedMatch]:
    """يعيد قائمة المنتجات المفقودة التي نملكها فعلاً باسم مكتوب مختلف.

    ``our_df`` = DataFrame كتالوجنا (يُستخرج عمود الاسم بـ_our_product_names_series)
    أو قائمة أسماء جاهزة. **قراءة-فقط تماماً** — لا يغيّر أي بيانات. أي خطأ ⇒ [].
    """
    try:
        if not isinstance(missing_df, pd.DataFrame) or missing_df.empty:
            return []
        if name_col not in missing_df.columns:
            return []
        our_names = (
            list(our_df) if isinstance(our_df, (list, tuple))
            else _our_product_names_series(our_df)
        )
        if not our_names:
            return []
        idx = _build_catalog_index(our_names)

        out: list[OwnedMatch] = []
        indices = list(missing_df.index)
        for pos, row in enumerate(missing_df.to_dict("records")):
            nm = str(row.get(name_col, "")).strip()
            if not nm:
                continue
            size_ml = extract_size(nm)
            conc = extract_type(nm)
            bucket = idx.get((_size_key(size_ml), conc))
            if not bucket:
                continue
            core = _core_tokens(nm)
            if not core:
                continue
            gender = extract_gender(nm)
            for (ocore, ogender, oraw) in bucket:
                if _gender_compatible(gender, ogender) and _core_equivalent(core, ocore):
                    out.append(OwnedMatch(
                        df_index=indices[pos],
                        comp_name=nm,
                        our_name=oraw,
                        size_ml=_size_key(size_ml),
                        conc=conc,
                    ))
                    break
        return out
    except Exception:
        return []


def owned_comp_names(matches: list[OwnedMatch]) -> set[str]:
    """مجموعة أسماء المنافس المُطابَقة (لإخفائها من المفقودات بأمان). خالص."""
    return {m.comp_name for m in matches if m.comp_name}


# ─────────────────────────────────────────────────────────────────────────────
# طبقة «محتمل» — استرجاع أعلى، ثقة أقل، للمراجعة اليدوية (لا إخفاء تلقائي)
# ─────────────────────────────────────────────────────────────────────────────
# الإرخاء الوحيد المسموح مقابل المطابق الصارم: طابِق الحجم/التركيز إذا تساويا
# **أو كان أحدهما مجهولاً** (فجوة استخراج) — لا يُطابَق أبداً معروفان مختلفان
# (30≠100، EDP≠EDT). مجموعة الكلمات المميّزة تبقى متساوية تماماً، فلا يعود خطأ
# النكهات المختلفة (كول ووتر ضد كول ووتر ويف). قياسٌ حيّ على 30,746 مفقوداً:
# 34 مرشّحاً إضافياً، عيّنة 24/25 صحيحة قاطعة (الباقي غامض ⇒ مراجعة المالك).


@dataclass(frozen=True)
class PossibleMatch:
    """مطابقة محتملة (أقلّ ثقة): نفس الكلمات المميّزة والجنس، لكن الحجم أو التركيز
    مجهولٌ على أحد الطرفين (فجوة استخراج لا تعارضٌ معروف). تحتاج مراجعة المالك."""
    df_index: Any        # فهرس الصف في missing_df (للإخفاء القابل للتراجع)
    comp_name: str       # الاسم كما يظهر في المفقودات
    our_name: str        # الاسم كما هو في كتالوجنا
    reason: str          # سبب كونها «محتمل» لا «مؤكّد» (حجم/تركيز مجهول)


def _size_compatible(a: int, b: int) -> bool:
    """متوافقان إذا تساويا أو كان أحدهما مجهولاً (0). لا يطابق معروفَين مختلفَين. خالص."""
    return a == b or a == 0 or b == 0


def _conc_compatible(a: str, b: str) -> bool:
    """متوافقان إذا تساويا أو كان أحدهما مجهولاً (''). لا يطابق EDP ضد EDT. خالص."""
    return a == b or not a or not b


def _index_by_core_len(our_names: list[str]) -> dict:
    """فهرس الكتالوج bucket=len(core) → [(core, gender, raw, size_key, conc)]. خالص.

    التجميع بعدد الكلمات المميّزة يكفي — تساوي المجموعات يشترط تساوي العدد أصلاً —
    فيقصر المقارنات على المرشّحين المحتملين فقط.
    """
    idx: dict = defaultdict(list)
    for nm in our_names:
        nm = str(nm)
        core = _core_tokens(nm)
        if not core:
            continue
        idx[len(core)].append(
            (core, extract_gender(nm), nm, _size_key(extract_size(nm)), extract_type(nm))
        )
    return idx


def find_possible_matches(
    missing_df: pd.DataFrame,
    our_df: Any,
    *,
    name_col: str = COMP_NAME_COL,
) -> list[PossibleMatch]:
    """مرشّحون «محتملون» (أقلّ ثقة من ``find_owned_matches``): تطابق الكلمات المميّزة
    والجنس، لكن الحجم أو التركيز **مجهول على أحد الطرفين**. يستبعد بنيوياً كل ما
    يلتقطه المطابق الصارم (يشترط فجوةً في بُعد واحد على الأقل) وكل معروفَين مختلفَين.
    **قراءة-فقط تماماً** — لا يغيّر أي بيانات. أي خطأ ⇒ [].
    """
    try:
        if not isinstance(missing_df, pd.DataFrame) or missing_df.empty:
            return []
        if name_col not in missing_df.columns:
            return []
        our_names = (
            list(our_df) if isinstance(our_df, (list, tuple))
            else _our_product_names_series(our_df)
        )
        if not our_names:
            return []
        by_len = _index_by_core_len(our_names)

        out: list[PossibleMatch] = []
        indices = list(missing_df.index)
        for pos, row in enumerate(missing_df.to_dict("records")):
            nm = str(row.get(name_col, "")).strip()
            if not nm:
                continue
            core = _core_tokens(nm)
            if not core:
                continue
            sk = _size_key(extract_size(nm))
            cc = extract_type(nm)
            gender = extract_gender(nm)
            for (ocore, ogender, oraw, osk, occ) in by_len.get(len(core), ()):
                if not _gender_compatible(gender, ogender):
                    continue
                if not (_size_compatible(sk, osk) and _conc_compatible(cc, occ)):
                    continue
                size_gap = (sk == 0 or osk == 0) and sk != osk
                conc_gap = (not cc or not occ) and cc != occ
                if not (size_gap or conc_gap):
                    continue  # لا فجوة ⇒ المطابق الصارم يلتقطه، لا نكرّره
                if not _core_equivalent(core, ocore):
                    continue
                reasons = []
                if size_gap:
                    reasons.append("الحجم غير مذكور على أحد الطرفين")
                if conc_gap:
                    reasons.append("التركيز غير مذكور على أحد الطرفين")
                out.append(PossibleMatch(
                    df_index=indices[pos],
                    comp_name=nm,
                    our_name=oraw,
                    reason=" · ".join(reasons),
                ))
                break
        return out
    except Exception:
        return []
