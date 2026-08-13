"""ui/state_manager.py — مدير حالة موحّد ومُنمَّط (بديل st.session_state الفوضوي).

يغلّف ``st.session_state`` خلف ``AppState`` مُنمّط + ``StateStore`` قابل للحقن،
فتُختبر منطق الحالة بقاموس عادي دون تشغيل Streamlit.

#PRESERVED_LOGIC: المفاتيح الثابتة للحذف الناعم بصيغة ``softdel_{اسم}``
(app.py soft-delete) — تبقى مستقرّة لإتاحة التراجع.
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import pandas as pd

from conf.constants import DATA_DIR

_STATE_KEY = "_app_state_v2"
_SNAPSHOT_PATH = DATA_DIR / "ui_session.json"   # الصيغة القديمة (ملف واحد) — تُقرأ للترحيل فقط
_META_NAME = "_meta.json"
_FORMAT = 3

# أسماء ملفّات آمنة (مفاتيح الأقسام إنجليزية، لكن لا نراهن على ذلك)
_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _snapshot_dir() -> str:
    """مجلّد اللقطة (صيغة 3): ``data/ui_session/``.

    مشتقّ من ``_SNAPSHOT_PATH`` **عمداً**: اختبارات المستودع ترقّع ذلك المسار وحده
    لعزل اللقطة الحيّة (``tests/conftest.py``)، فاشتقاق المجلّد منه يُبقي العزل
    عاملاً بلا تعديل أي اختبار. يقبل str أو Path (اختباران يمرّران str).
    """
    return os.path.splitext(str(_SNAPSHOT_PATH))[0]


def _frame_file(key: str) -> str:
    """اسم ملف آمن لمفتاح إطار (``sections.price_raise`` ⇒ ``sections.price_raise.json``)."""
    return "".join(c if c in _SAFE_CHARS else "_" for c in key) + ".json"


def _atomic_write(path: str, text: str) -> None:
    """كتابة ذرّية: ملف مؤقّت ثم ``os.replace`` — لا يوجد ملف نصف مكتوب أبداً."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _frame_fingerprint(df: pd.DataFrame) -> tuple:
    """بصمة رخيصة تجيب: هل تغيّر هذا الإطار منذ آخر كتابة؟ (هوية · طول · أعمدة).

    #PRESERVED_LOGIC: صحّتها مبنيّة على أن الواجهة **تستبدل** الإطار بكائن جديد
    ولا تعدّله في مكانه. مُتحقَّق منه بـgrep (2026-07-25): صفر ``.at[]`` أو
    ``inplace=True`` على ``state.sections`` / ``state.missing_df`` /
    ``state.our_catalog`` في المستودع كلّه؛ الموضعان الوحيدان في
    ``ui/pages/missing.py`` يعملان على ``view_df.loc[...].copy()``.
    يحرس هذا الافتراضَ ``tests/test_snapshot_split.py``.

    لماذا ليست بصمة محتوى: ``pd.util.hash_pandas_object`` **ينهار** على الأقسام
    الخمسة كلّها (خلايا تحوي قوائم: ``TypeError: unhashable type: 'list'``)
    ويكلّف 0.47s لكل نقرة — قياس 2026-07-25.
    """
    return (id(df), int(len(df)), tuple(str(c) for c in df.columns))


def _serialize_snapshot(snapshot: dict) -> dict:
    """Convert snapshot to JSON-serializable dict."""
    result = {}
    # force_ascii=False: افتراضي باندا يحوّل كل حرف عربي إلى ‎\uXXXX (تضخيم ~3×
    # لبيانات عربية الأغلب — قياس 2026-07-09: كتالوجنا 187MB⇒84MB بدورة مطابقة).
    for key, value in snapshot.items():
        if isinstance(value, pd.DataFrame):
            result[key] = {"__type__": "DataFrame", "data": value.to_json(orient="split", force_ascii=False)}
        elif isinstance(value, dict):
            result[key] = {}
            for k, v in value.items():
                if isinstance(v, pd.DataFrame):
                    result[key][k] = {"__type__": "DataFrame", "data": v.to_json(orient="split", force_ascii=False)}
                else:
                    result[key][k] = v
        elif isinstance(value, set):
            result[key] = {"__type__": "set", "data": list(value)}
        elif hasattr(value, "model_dump"):
            result[key] = {"__type__": "pydantic", "class": value.__class__.__name__, "data": value.model_dump(mode="json")}
        else:
            result[key] = value
    return result


def _deserialize_snapshot(data: dict) -> dict:
    """Convert JSON-serialized dict back to original types."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            type_tag = value.get("__type__")
            if type_tag == "DataFrame":
                result[key] = pd.read_json(io.StringIO(value["data"]), orient="split")
            elif type_tag == "set":
                result[key] = set(value["data"])
            elif type_tag == "pydantic":
                class_name = value["class"]
                raw_data = value["data"]
                if class_name == "AnalysisResult":
                    from core.models import AnalysisResult
                    result[key] = AnalysisResult.model_validate(raw_data)
                else:
                    result[key] = raw_data
            else:
                result[key] = {}
                for k, v in value.items():
                    if isinstance(v, dict) and v.get("__type__") == "DataFrame":
                        result[key][k] = pd.read_json(io.StringIO(v["data"]), orient="split")
                    else:
                        result[key][k] = v
        elif isinstance(value, list):
            result[key] = list(value)
        else:
            result[key] = value
    return result

def snapshot_exists() -> bool:
    """هل توجد لقطة محفوظة على القرص (بأيّ صيغة)؟"""
    return (os.path.exists(os.path.join(_snapshot_dir(), _META_NAME))
            or os.path.exists(str(_SNAPSHOT_PATH)))


def snapshot_size_bytes() -> int:
    """حجم اللقطة الكلّي بالبايت (مجموع ملفّات المجلّد، أو الملف القديم)."""
    dirpath = _snapshot_dir()
    if os.path.exists(os.path.join(dirpath, _META_NAME)):
        total = 0
        for name in os.listdir(dirpath):
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
        return total
    try:
        return os.path.getsize(str(_SNAPSHOT_PATH))
    except OSError:
        return 0


def load_snapshot_raw() -> dict:
    """يعيد اللقطة بالشكل **الخام القديم** من أيّ صيغة على القرص — جسر توافق.

    أدوات التقارير (``scripts/perf_baseline`` · ``redistribution_dryrun`` ·
    ``report_c5_brand_category_fixes`` · ``_run_review_full`` · ``e2e_health_check``)
    كانت تفتح ``ui_session.json`` مباشرةً بـ``json.load``. بعد تقسيم اللقطة
    (صيغة 3) تستدعي هذه الدالة بدلاً منه فتحصل على **نفس البنية حرفياً**
    (``{"__type__": "DataFrame", "data": "..."}``) دون تغيير منطقها ودون
    تحميل pandas. يعيد ``{}`` إن لم توجد لقطة.
    """
    dirpath = _snapshot_dir()
    meta_path = os.path.join(dirpath, _META_NAME)
    if not os.path.exists(meta_path):
        try:
            with open(str(_SNAPSHOT_PATH), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        out: dict = {"our_catalog": None, "missing_df": None, "sections": {}}
        for key, fname in (meta.get("frames") or {}).items():
            fpath = os.path.join(dirpath, str(fname))
            if not os.path.exists(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as handle:
                payload = {"__type__": "DataFrame", "data": handle.read()}
            if key.startswith("sections."):
                out["sections"][key[len("sections."):]] = payload
            else:
                out[key] = payload
        out["sections"].update(meta.get("sections_other") or {})
        ar = meta.get("analysis_results")
        out["analysis_results"] = (
            {"__type__": "pydantic", "class": ar["class"], "data": ar["data"]}
            if isinstance(ar, dict) and "class" in ar else None
        )
        for key in ("processed_price_skus", "processed_missing_urls", "hidden_products"):
            out[key] = {"__type__": "set", "data": list(meta.get(key) or [])}
        out["processed_log"] = list(meta.get("processed_log") or [])
        return out
    except Exception:
        return {}


# رموز روابط غير صالحة: لا تُعدّ «رابط منتج مُعالَج». str(NaN)=="nan" لرابط مفقود،
# ولو دخلت المجموعة لطابقت كل صفّ بلا رابط فأخفت كل المفقودات.
_JUNK_LINK_TOKENS = frozenset({"", "nan", "none", "null", "na", "0", "-"})


def row_image_url(row: Any) -> str:
    """أول رابط صورة صالح من صفّ منتج (نفس مفاتيح الإرسال:
    ``صورة_المنافس`` / ``صورة المنتج`` / ``image_url``). خالص — يعيد "" إن غاب.

    يُمرَّر إلى ``log_action(image=...)`` كي تعرض بطاقة «تمت المعالجة» صورة المنتج.
    """
    get = getattr(row, "get", None)
    if not callable(get):
        return ""
    for k in ("صورة_المنافس", "صورة المنتج", "image_url"):
        v = get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("nan", "none"):
            return s
    return ""


def stable_key(product_name: str) -> str:
    """مفتاح حذف ناعم مستقرّ لمنتج. #PRESERVED_LOGIC softdel_{product_name}."""
    return f"softdel_{str(product_name).strip()}"


class StateStore(Protocol):
    """واجهة مخزن حالة (get/set/contains)."""

    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


class DictStore:
    """مخزن قاموسي للاختبار (بلا Streamlit)."""

    def __init__(self, data: Optional[dict[str, Any]] = None) -> None:
        self._data: dict[str, Any] = data if data is not None else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


class StreamlitStore:
    """مخزن يغلّف ``st.session_state`` (استيراد كسول)."""

    def __init__(self) -> None:
        import streamlit as st

        self._ss = st.session_state

    def get(self, key: str, default: Any = None) -> Any:
        return self._ss.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._ss[key] = value


@dataclass
class AppState:
    """حالة التطبيق المُنمّطة. تُحمَّل/تُحفَظ عبر ``StateStore``."""

    our_catalog: Optional[pd.DataFrame] = None
    competitor_catalogs: dict[str, pd.DataFrame] = field(default_factory=dict)
    analysis_results: Optional[Any] = None
    job_id: Optional[str] = None
    is_analysis_running: bool = False
    hidden_products: set[str] = field(default_factory=set)
    processed_price_skus: set[str] = field(default_factory=set)
    processed_missing_urls: set[str] = field(default_factory=set)
    # سجلّ شفّاف لكل إجراء منفّذ (لعرض «تمت المعالجة» مع تفصيل التعديل).
    processed_log: list[dict] = field(default_factory=list)
    current_page: str = "dashboard"
    # نتائج التحليل الجاهزة للعرض (يملؤها الموجّه بعد run_analysis).
    sections: dict[str, Any] = field(default_factory=dict)
    missing_df: Optional[pd.DataFrame] = None

    # بصمات آخر كتابة لكل إطار — تُبقي ``persist_results`` كاتباً لما تغيّر فقط.
    # ``init=False``: ليست جزءاً من واجهة البناء ولا تُحفظ على القرص.
    _frame_fp: dict[str, tuple] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )

    # ── الحذف الناعم (مفاتيح مستقرّة) ──
    def hide(self, product_name: str) -> None:
        self.hidden_products.add(stable_key(product_name))

    def unhide(self, product_name: str) -> None:
        self.hidden_products.discard(stable_key(product_name))

    def is_hidden(self, product_name: str) -> bool:
        return stable_key(product_name) in self.hidden_products

    # ── تتبّع المعالجة ──
    def mark_price_processed(self, sku: str) -> None:
        self.processed_price_skus.add(str(sku).strip())

    def mark_missing_processed(self, url: str) -> None:
        # حماية: لا تُخزّن روابط فارغة/غير صالحة ("nan"/"none"/"") — وإلا تطابق
        # active_missing كلَّ صف بلا رابط (str(NaN)=="nan") فتختفي كل المفقودات.
        u = str(url).strip()
        if u.lower() in _JUNK_LINK_TOKENS:
            return
        self.processed_missing_urls.add(u)

    def is_price_processed(self, sku: str) -> bool:
        return str(sku).strip() in self.processed_price_skus

    # ── سجلّ الإجراءات (لصفحة «تمت المعالجة» مع تفصيل التعديل) ──
    def log_action(
        self, *, key: str, name: str, action: str, detail: str = "", kind: str = "",
        row_data: dict | None = None, image: str = "",
    ) -> None:
        """يسجّل إجراءً منفّذاً على منتج (أحدث أولاً، بلا تكرار لنفس المفتاح+الإجراء).

        ``kind`` ∈ {price, missing, hidden} يحدّد كيفية التراجع. لا انهيار على قيمة فارغة.
        ``row_data`` بيانات بصرية اختيارية: {image, price, brand, section, comp_name, comp_price}.
        ``image`` رابط صورة المنتج — يُخزَّن في ``row_data["image"]`` لتعرضه بطاقة
        «تمت المعالجة»؛ إن غاب تبقى الأيقونة الافتراضية (لا كسر).
        """
        from datetime import datetime

        normalized_key = str(key).strip()
        self.processed_log = [
            entry for entry in self.processed_log
            if not (entry.get("key") == normalized_key and entry.get("action") == action)
        ]
        entry: dict = {
            "key": normalized_key, "name": str(name).strip(), "action": str(action),
            "detail": str(detail), "kind": str(kind),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        rd = dict(row_data) if isinstance(row_data, dict) else {}
        if image and not rd.get("image"):
            rd["image"] = str(image)
        if rd:
            entry["row_data"] = rd
        self.processed_log.insert(0, entry)

    def remove_log(self, key: str) -> None:
        """يحذف كل سجلّات مفتاح (يُستخدم عند التراجع)."""
        normalized_key = str(key).strip()
        self.processed_log = [
            entry for entry in self.processed_log if entry.get("key") != normalized_key
        ]

    # ── الحفظ الدائم على القرص (يَنجو من إعادة التشغيل) ──
    def persist_results(self, *, full: bool = False) -> bool:
        """يحفظ نتائج التحليل على القرص: **ملفّ لكل إطار** + ``_meta.json`` صغير.

        ⚠️ **تصحيح توثيق (2026-07-25):** الادّعاء السابق «يُستدعى مرة بعد كل تحليل
        ناجح فقط (لا في كل تفاعل)» كان **خاطئاً** — grep يُظهر ~22 موضع نداء في
        الواجهة: كل إرسال سعر · إخفاء · قرار مراجعة · سلة محذوفات · إعادة توزيع.
        فكان كل نقرة تُعيد كتابة اللقطة كاملة (72.8 م.ب) بكلفة **1.42 ثانية**
        مقيسة (تسلسل 0.61s + ``json.dumps`` 1.21s تُهرّب النصّ المُرمَّز مرّة ثانية
        + كتابة قرص 0.33s).

        لذلك: ملفّ مستقلّ لكل إطار بنفس حمولة ``to_json(orient="split")`` حرفياً
        (صفر تغيّر في الأنواع)، ويُكتب **ما تغيّر فقط** بدلالة ``_frame_fingerprint``.
        قياس ما بعد التغيير على اللقطة الحيّة: إخفاء منتج 0.009s · قرار مراجعة
        0.041s · قرار في أكبر قسم 0.316s.

        ``full=True`` يُجبر كتابة كل الإطارات (بعد تحليل جديد، وعند الترحيل من
        الصيغة القديمة). كل ملف يُكتب ذرّياً، و``_meta.json`` يُكتب **أخيراً** فلا
        يُعلَن إطار قبل أن يوجد ملفّه. يُرجع True عند نجاح الكتابة.
        """
        if not self.sections:
            return False
        # تخسيس اللقطة: عمودا الوصف («الوصف» ~95MB، «وصف صفحة المنتج» ~73MB —
        # grep 2026-07-09: صفر قرّاء في المستودع) يُسقَطان من النسخة المُسلسَلة فقط —
        # drop يعيد نسخة والكتالوج الحي لا يُمَس، وإعادة رفع الكتالوج تعيدهما.
        oc = self.our_catalog
        _slim = [c for c in ("الوصف", "وصف صفحة المنتج")
                 if c in getattr(oc, "columns", ())]
        if oc is not None and _slim:
            oc = oc.drop(columns=_slim)

        frames: dict[str, pd.DataFrame] = {}
        others: dict[str, Any] = {}     # قيم أقسام ليست DataFrame (تبقى في _meta)
        if isinstance(oc, pd.DataFrame):
            frames["our_catalog"] = oc
        if isinstance(self.missing_df, pd.DataFrame):
            frames["missing_df"] = self.missing_df
        for key, val in self.sections.items():
            if isinstance(val, pd.DataFrame):
                frames[f"sections.{key}"] = val
            else:
                others[str(key)] = val

        dirpath = _snapshot_dir()
        try:
            os.makedirs(dirpath, exist_ok=True)
            files: dict[str, str] = {}
            for key, frame in frames.items():
                fname = _frame_file(key)
                fpath = os.path.join(dirpath, fname)
                fingerprint = _frame_fingerprint(frame)
                if (full or self._frame_fp.get(key) != fingerprint
                        or not os.path.exists(fpath)):
                    _atomic_write(
                        fpath, frame.to_json(orient="split", force_ascii=False),
                    )
                    self._frame_fp[key] = fingerprint
                files[key] = fname

            ar = self.analysis_results
            meta = {
                "__format__": _FORMAT,
                "frames": files,
                "sections_other": others,
                "analysis_results": (
                    {"class": ar.__class__.__name__, "data": ar.model_dump(mode="json")}
                    if hasattr(ar, "model_dump") else None
                ),
                "processed_price_skus": sorted(self.processed_price_skus),
                "processed_missing_urls": sorted(self.processed_missing_urls),
                "hidden_products": sorted(self.hidden_products),
                "processed_log": self.processed_log,
            }
            _atomic_write(
                os.path.join(dirpath, _META_NAME),
                json.dumps(meta, ensure_ascii=False),
            )
            self._prune_stale_frames(dirpath, set(files.values()))
            return True
        except Exception:
            logging.getLogger("state_manager").exception("persist_results فشل حفظ اللقطة")
            return False

    @staticmethod
    def _prune_stale_frames(dirpath: str, keep: set[str]) -> None:
        """يحذف ملفّات إطارات لم تعد ضمن اللقطة (قسم اختفى بين تحليلين). محروس."""
        allowed = set(keep) | {_META_NAME}
        try:
            names = os.listdir(dirpath)
        except OSError:
            return
        for name in names:
            if name.endswith(".json") and name not in allowed:
                try:
                    os.remove(os.path.join(dirpath, name))
                except OSError:
                    pass

    def restore_results(self) -> bool:
        """يستعيد آخر نتائج محفوظة من القرص إن خلت الجلسة منها. يُرجع True عند الاستعادة.

        يقرأ صيغة 3 (المجلّد) أولاً؛ فإن غابت قرأ الملف الواحد القديم و**رحّل**
        اللقطة تلقائياً إلى الصيغة الجديدة — فلا يفقد المالك تحليله عند الترقية.
        """
        if self.sections:
            return False
        if self._restore_v3():
            return True
        if self._restore_legacy():
            self.persist_results(full=True)
            self._retire_legacy_file()
            return True
        return False

    def _restore_v3(self) -> bool:
        """يقرأ صيغة المجلّد (ملف لكل إطار). يعيد False إن غابت أو تعذّرت."""
        dirpath = _snapshot_dir()
        meta_path = os.path.join(dirpath, _META_NAME)
        if not os.path.exists(meta_path):
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            files = meta.get("frames") or {}
            loaded: dict[str, pd.DataFrame] = {}
            lost: list[str] = []
            for key, fname in files.items():
                fpath = os.path.join(dirpath, str(fname))
                if not os.path.exists(fpath):
                    lost.append(str(fname))
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8") as handle:
                        loaded[key] = pd.read_json(
                            io.StringIO(handle.read()), orient="split",
                        )
                except Exception:
                    # تخطّي إطار تالف بصمت يعني لوحةً تبدو **أصغر** لا معطوبة،
                    # وهذا أخطر من الفشل الصريح لأنه لا يترك أثراً للتشخيص.
                    lost.append(str(fname))
                    logging.getLogger("state_manager").exception(
                        "تعذّرت قراءة إطار اللقطة %s", fname,
                    )
            if lost:
                logging.getLogger("state_manager").warning(
                    "لقطة ناقصة: %d إطاراً من %d غير مقروء (%s) — "
                    "اللوحة ستعرض أقل ممّا حُفظ",
                    len(lost), len(files), "، ".join(lost[:5]),
                )
            sections: dict[str, Any] = {
                key[len("sections."):]: frame
                for key, frame in loaded.items() if key.startswith("sections.")
            }
            sections.update(meta.get("sections_other") or {})
            if not sections:
                return False

            self.our_catalog = loaded.get("our_catalog")
            self.missing_df = loaded.get("missing_df")
            self.sections = sections
            ar = meta.get("analysis_results")
            self.analysis_results = None
            if isinstance(ar, dict):
                if ar.get("class") == "AnalysisResult":
                    from core.models import AnalysisResult
                    self.analysis_results = AnalysisResult.model_validate(ar["data"])
                else:
                    self.analysis_results = ar.get("data")
            self.processed_price_skus = set(meta.get("processed_price_skus") or [])
            self.processed_missing_urls = {
                str(u).strip() for u in (meta.get("processed_missing_urls") or [])
                if str(u).strip().lower() not in _JUNK_LINK_TOKENS
            }
            self.hidden_products = set(meta.get("hidden_products") or [])
            self.processed_log = list(meta.get("processed_log") or [])
            # ما على القرص يطابق ما في الذاكرة الآن ⇒ ابصمه كي لا تُعيد أوّلُ
            # نقرةٍ كتابةَ كل الإطارات بلا سبب.
            self._frame_fp = {
                key: _frame_fingerprint(frame) for key, frame in loaded.items()
            }
            self._reconcile_section_counts()
            return True
        except Exception:
            logging.getLogger("state_manager").exception(
                "تعذّرت استعادة لقطة صيغة 3 من %s", dirpath,
            )
            return False

    def _restore_legacy(self) -> bool:
        """يقرأ الصيغة القديمة (``ui_session.json`` ملفاً واحداً). للترحيل فقط."""
        if not os.path.exists(_SNAPSHOT_PATH):
            return False
        try:
            with open(_SNAPSHOT_PATH, "r", encoding="utf-8") as handle:
                snap = _deserialize_snapshot(json.load(handle))
        except Exception:
            return False
        if not isinstance(snap, dict) or not snap.get("sections"):
            return False
        self.our_catalog = snap.get("our_catalog")
        self.sections = snap.get("sections") or {}
        self.missing_df = snap.get("missing_df")
        self.analysis_results = snap.get("analysis_results")
        self.processed_price_skus = set(snap.get("processed_price_skus") or set())
        self.processed_missing_urls = {
            str(u).strip() for u in (snap.get("processed_missing_urls") or set())
            if str(u).strip().lower() not in _JUNK_LINK_TOKENS
        }
        self.hidden_products = set(snap.get("hidden_products") or set())
        self.processed_log = list(snap.get("processed_log") or [])
        self._reconcile_section_counts()
        return True

    @staticmethod
    def _retire_legacy_file() -> None:
        """يُعيد تسمية الملف القديم بعد ترحيل ناجح — يبقى شبكةَ أمان ولا يُقرأ ثانيةً.

        لا يُحذف (المتجر حيّ)، ولا يبقى باسمه القديم كي لا تظنّه أداةُ نسخٍ حيّاً.
        """
        src = str(_SNAPSHOT_PATH)
        if not os.path.exists(src):
            return
        try:
            os.replace(src, f"{src}.pre-v3")
        except OSError:
            pass

    def _reconcile_section_counts(self) -> None:
        """يُصلح عدّاد لوحة التحكم إن تجمّد عن الأقسام الحيّة (لقطة قديمة).

        لقطة أُنتِجت قبل مزامنة العدّاد قد تحمل ``section_counts`` قديماً لا يطابق
        ``sections`` (مثال: زر إنقاذ قديم نقل صفوفاً ولم يحدّث العدّاد). نُعيد بناء
        عدّاد الأقسام الخمسة من الطول الحيّ ونُبقي ``missing`` كما هو. محروس.
        """
        ar = self.analysis_results
        secs = self.sections
        if ar is None or not hasattr(ar, "section_counts") or not isinstance(secs, dict):
            return
        try:
            from core.enums import SectionType
            for key in ("price_raise", "price_lower", "approved", "review", "excluded"):
                df = secs.get(key)
                if df is not None:
                    ar.section_counts[SectionType(key)] = len(df)
        except Exception:  # noqa: BLE001 — العدّاد تجميلي؛ فشله لا يمنع الاستعادة
            pass

    def save(self, store: StateStore) -> None:
        """يحفظ الحالة في المخزن (مفتاح واحد يحمل الكائن)."""
        store.set(_STATE_KEY, self)

    @classmethod
    def load(cls, store: StateStore) -> "AppState":
        """يحمّل الحالة أو ينشئ جديدة مع تطبيع الأنواع."""
        existing = store.get(_STATE_KEY)
        if isinstance(existing, cls):
            existing._normalize()
            return existing
        state = cls()
        state.save(store)
        return state

    def _normalize(self) -> None:
        """يضمن أنواع المجموعات (حماية من حالة قديمة تالفة)."""
        # كائن حالة نجا في الجلسة من نسخة كود أقدم لا يحمل هذا الحقل.
        if not isinstance(getattr(self, "_frame_fp", None), dict):
            self._frame_fp = {}
        self.hidden_products = set(self.hidden_products or set())
        self.processed_price_skus = set(self.processed_price_skus or set())
        # تنظيف الروابط الملوّثة من حالة قديمة ("nan"/""/...) — تُخفي كل المفقودات.
        self.processed_missing_urls = {
            str(u).strip() for u in (self.processed_missing_urls or set())
            if str(u).strip().lower() not in _JUNK_LINK_TOKENS
        }
        self.processed_log = list(self.processed_log or [])
