"""ui/reconcile.py — مطابقة المفقودات بنتائج فحص المستخدم الخارجي (المرحلة A، بلا AI/كلفة).

فحص المستخدم دفعة المفقودات «المؤكدة» (green) بأدوات خارجية وأنتج ملفّين في
``DATA_DIR/reconcile/``:
- ``missing_DUPLICATE.csv``: منافسون تبيّن أنهم يطابقون منتجاً موجوداً في متجرنا
  (عمود ``منتج_مطابق_محتمل``) ⇒ يُزالون من المفقودات ويُلحَقون ببطاقة منتجنا.
- ``missing_UNIQUE.csv``: منافسون مفقودون فعلاً ⇒ يبقون في المفقودات (مؤكد).

تُطبَّق على ``state.missing_df`` و``state.sections`` **عرضياً فقط**:
- إزالة صفوف المكرر من المفقودات (مفتاح ``COL_COMP_NAME``) — لا تُخفى المراجعة.
- إلحاق كل منافس مكرر بقائمة ``جميع_المنافسين`` في صفّ منتجنا المطابق
  (``COL_OUR_NAME``) ورفع ``COL_COMP_COUNT`` للصفوف المتغيّرة فقط — **بدون** تغيير
  ``القرار`` أو إعادة تشغيل المحرّك (لا يكسر توازن التدقيق).

كل الدوال خالصة وقابلة للاختبار بمعطيات تركيبية (لا قراءة CSV/شبكة/AI في الاختبار).
إعادة التطبيق آمنة (idempotent: لا تكرار إلحاق لمنافس بنفس name+competitor).

#PRESERVED_LOGIC: ``جميع_المنافسين`` عمود عرض المحرّك (مفاتيح إنجليزية)، ليس في conf.constants.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from conf.constants import (
    COL_BRAND, COL_COMP_COUNT, COL_COMP_IMAGE, COL_COMP_NAME, COL_COMP_PRICE,
    COL_COMP_STORE, COL_OUR_NAME,
)

# أعمدة العرض/الفحص غير الموجودة في conf.constants (محلية، #PRESERVED_LOGIC)
_COL_ALL_COMP = "جميع_المنافسين"     # list[dict] بمفاتيح إنجليزية (مسار التسعير)
_COL_MATCHED = "منتج_مطابق_محتمل"    # اسم منتجنا المطابق (ملفّات الفحص)
_COL_SIM = "درجة_تشابه_محسوبة"
_COL_SIM_ALT = "درجة_التشابه"
_COL_COMP_SIZE = "حجم_المنافس_مل"
_COL_COMP_TYPE = "نوع_المنافس"
_COL_COMP_GENDER = "جنس_المنافس"
_CONFIRM_FLAG = "_تطابق_مؤكد"         # وسم على المنافس الملحَق (يتجاهله مُطبِّع البطاقة)

_RECONCILE_DIR = "reconcile"
_DUP_FILE = "missing_DUPLICATE.csv"
_UNI_FILE = "missing_UNIQUE.csv"
_AI_DUP_FILE = "review_ai_DUPLICATE.csv"  # مكررات المراجعة التي أكّدها AI (المرحلة B)


def _s(value: Any) -> str:
    """نص آمن من None/NaN/'nan'/'none'/''."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "<na>") else text


def _to_int(value: Any) -> int:
    """عدد صحيح آمن (0 عند التعذّر)."""
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return 0


def _as_list(value: Any) -> list:
    """قائمة منافسين آمنة من list/JSON-str/repr-str/None/NaN."""
    if isinstance(value, list):
        return list(value)
    text = _s(value)
    if not text.startswith("["):
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        try:
            import ast
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return []
    return parsed if isinstance(parsed, list) else []


def _comp_identity(item: Any) -> tuple[str, str]:
    """هوية المنافس (name, competitor) لكشف التكرار عند الإلحاق."""
    if not isinstance(item, dict):
        return ("", "")
    return (_s(item.get("name")), _s(item.get("competitor")))


def duplicate_keys(dup_df: Optional[pd.DataFrame]) -> set[str]:
    """مجموعة أسماء المنافسين المكرّرة (COL_COMP_NAME). آمنة من None/غياب العمود."""
    if not isinstance(dup_df, pd.DataFrame) or dup_df.empty \
            or COL_COMP_NAME not in dup_df.columns:
        return set()
    return {key for key in (_s(v) for v in dup_df[COL_COMP_NAME]) if key}


def clean_missing(missing_df: Any, dup_keys: set[str]) -> pd.DataFrame:
    """يزيل صفوف المكرر من المفقودات (يُبقي كل ما عداها — لا يُخفي المراجعة). خالص."""
    if not isinstance(missing_df, pd.DataFrame) or missing_df.empty:
        return missing_df if isinstance(missing_df, pd.DataFrame) else pd.DataFrame()
    if not dup_keys or COL_COMP_NAME not in missing_df.columns:
        return missing_df
    keys = missing_df[COL_COMP_NAME].map(_s)
    return missing_df[~keys.isin(dup_keys)].reset_index(drop=True)


def _competitor_dict(row: dict) -> dict[str, Any]:
    """يبني قاموس منافس بمفاتيح ``جميع_المنافسين`` الإنجليزية من صفّ مكرر."""
    image = _s(row.get(COL_COMP_IMAGE))
    return {
        "name": _s(row.get(COL_COMP_NAME)),
        "competitor": _s(row.get(COL_COMP_STORE)),
        "price": _s(row.get(COL_COMP_PRICE)),
        "image_url": image,
        "thumb": image,
        "product_url": "",
        "brand": _s(row.get(COL_BRAND)),
        "size": _s(row.get(_COL_COMP_SIZE)),
        "type": _s(row.get(_COL_COMP_TYPE)),
        "gender": _s(row.get(_COL_COMP_GENDER)),
        "score": _s(row.get(_COL_SIM)) or _s(row.get(_COL_SIM_ALT)),
        _CONFIRM_FLAG: True,
    }


def attachments_from_duplicates(dup_df: Optional[pd.DataFrame]) -> dict[str, list[dict]]:
    """خريطة {اسم_منتجنا المطابق: [قاموس_منافس,...]} من ملف المكرر. آمنة."""
    if not isinstance(dup_df, pd.DataFrame) or dup_df.empty \
            or _COL_MATCHED not in dup_df.columns:
        return {}
    out: dict[str, list[dict]] = {}
    for _, row in dup_df.iterrows():
        store = _s(row.get(_COL_MATCHED))
        if not store:
            continue
        out.setdefault(store, []).append(_competitor_dict(row.to_dict()))
    return out


def attach_to_sections(sections: Any, attach_map: dict[str, list[dict]]) -> Any:
    """يلحق المنافسين المكررين ببطاقات منتجاتنا المطابقة عبر كل الأقسام. خالص.

    يعيد قاموس أقسام جديداً (لا يطفر الأصل). idempotent: لا يعيد إلحاق منافس
    بنفس (name, competitor). يرفع ``COL_COMP_COUNT`` للصفوف المتغيّرة فقط
    (لا يمسّ عدّ الصفوف غير المتأثّرة).
    """
    if not isinstance(sections, dict) or not attach_map:
        return sections
    new_sections: dict[str, Any] = {}
    for key, df in sections.items():
        if not isinstance(df, pd.DataFrame) or df.empty \
                or COL_OUR_NAME not in df.columns or _COL_ALL_COMP not in df.columns:
            new_sections[key] = df
            continue
        work = df.copy()
        comps = work[_COL_ALL_COMP].tolist()
        names = work[COL_OUR_NAME].map(_s).tolist()
        has_count = COL_COMP_COUNT in work.columns
        counts = work[COL_COMP_COUNT].tolist() if has_count else [0] * len(work)
        changed = False
        for i, pname in enumerate(names):
            extra = attach_map.get(pname)
            if not extra:
                continue
            current = _as_list(comps[i])
            seen = {_comp_identity(c) for c in current}
            added = [d for d in extra if _comp_identity(d) not in seen]
            if not added:
                continue
            comps[i] = current + added
            counts[i] = _to_int(counts[i]) + len(added)
            changed = True
        if changed:
            work[_COL_ALL_COMP] = comps
            if has_count:
                work[COL_COMP_COUNT] = counts
        new_sections[key] = work
    return new_sections


def apply_reconciliation(
    missing_df: Any,
    sections: Any,
    *,
    dup_df: Optional[pd.DataFrame],
    uni_df: Optional[pd.DataFrame] = None,
) -> tuple[Any, Any, dict[str, int]]:
    """يطبّق المطابقة كاملة (إزالة مكرر + إلحاق بالبطاقات). خالص ⇒ يعيد (missing, sections, stats)."""
    dkeys = duplicate_keys(dup_df)
    new_missing = clean_missing(missing_df, dkeys)
    attach_map = attachments_from_duplicates(dup_df)
    new_sections = attach_to_sections(sections, attach_map)
    n_before = len(missing_df) if isinstance(missing_df, pd.DataFrame) else 0
    n_after = len(new_missing) if isinstance(new_missing, pd.DataFrame) else 0
    attached = sum(len(v) for v in attach_map.values())
    stats = {
        "removed_duplicates": n_before - n_after,
        "attached_competitors": attached,
        "attached_products": len(attach_map),
        "missing_before": n_before,
        "missing_after": n_after,
        "unique_listed": len(uni_df) if isinstance(uni_df, pd.DataFrame) else 0,
    }
    return new_missing, new_sections, stats


def load_reconcile_frames(
    data_dir: Any,
) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """يحمّل ملفّات الفحص من ``data_dir/reconcile``.

    يدمج المكرر اليدوي (``missing_DUPLICATE.csv``) مع مكرر AI للمراجعة
    (``review_ai_DUPLICATE.csv``) في dup_df واحد (إن وُجد أيٌّ منهما). يعيد
    (None, None) إن غاب الاثنان. (uni من ``missing_UNIQUE.csv`` لإحصاء فقط.)
    """
    base = Path(data_dir) / _RECONCILE_DIR
    frames: list[pd.DataFrame] = []
    for name in (_DUP_FILE, _AI_DUP_FILE):
        path = base / name
        if path.exists():
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
            if not frame.empty:
                frames.append(frame)
    if not frames:
        return None, None
    dup = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    uni_path = base / _UNI_FILE
    uni = (
        pd.read_csv(uni_path, dtype=str, keep_default_na=False)
        if uni_path.exists() else None
    )
    return dup, uni


def reconcile_state(state: Any) -> Optional[dict[str, int]]:
    """يحمّل ملفّات الفحص (إن وُجدت) ويطبّقها على ``state``. آمن/idempotent.

    يعيد قاموس الإحصاء عند التطبيق، أو ``None`` إن غابت الملفّات أو تعذّر شيء
    (لا انهيار للواجهة مهما كان محتوى الملف).
    """
    try:
        from conf.constants import DATA_DIR

        dup_df, uni_df = load_reconcile_frames(DATA_DIR)
        if dup_df is None or dup_df.empty:
            return None
        new_missing, new_sections, stats = apply_reconciliation(
            getattr(state, "missing_df", None), getattr(state, "sections", None),
            dup_df=dup_df, uni_df=uni_df,
        )
        state.missing_df = new_missing
        state.sections = new_sections
        return stats
    except Exception:  # noqa: BLE001 — لا انهيار للتحليل بسبب ملف فحص تالف
        return None
