"""services/analysis_runner.py — مشغّل التحليل الخلفي المحصّن ضد الانقطاع.

الجذر الذي يعالجه (نسختان من الحادثة نفسها، 2026-07-12):
1) التحليل كان يعمل داخل دورة عرض Streamlit، فأي ضغطة زر/تحديث صفحة أثناء
   الدقائق الطويلة كان يفصل النتائج عن الحفظ (ضياع عرض تحليل ~110 دقيقة).
2) الإصلاح الأول (خيط داخل نفس عملية الخادم) أزال هذا لكنه أبقى مشكلة أعمق:
   خيط Python يتشارك الـGIL مع خادم الويب — عمل كثيف لساعات خنق قدرة
   Streamlit على الرد فظهر "Connection timed out" رغم أن كل شيء يعمل فعلياً.

الإصلاح الصحيح هنا: التحليل يعمل في **عملية نظام تشغيل منفصلة تماماً**
(``subprocess.Popen`` معزولة) لا خيط — فلا GIL مشترك مع الخادم مهما طال
الحساب، ولا يتأثر حتى بإعادة تشغيل خادم Streamlit نفسه. بروتوكول التتبع
يبقى ملفياً كما هو (``data/analysis_progress.json``) — هذا ما يجعل التبديل
من خيط إلى عملية منفصلة تغييراً داخلياً بحتاً لا يمس الواجهة.

- لا يتأثر بأي تفاعل متصفح (rerun/refresh/إغلاق تبويب) ولا بإعادة تشغيل الخادم.
- يكتب تقدّمه في ملف ذرّي تقرأه الواجهة (``data/analysis_progress.json``).
- يحفظ النتائج بنفسه عبر ``AppState.persist_results`` (كتابة ذرّية) مع دمج
  حقول الجلسة المحفوظة (hidden_products/processed_*) كي لا يمحوها — #DATA_CONSERVATION.
- قفل مزدوج (ملف heartbeat + مرجع العملية) يمنع تحليلين متوازيين.

تدهور رشيق: فشل هذا المشغّل لا يمس المسار المتزامن القديم في dashboard —
الواجهة ترتد إليه تلقائياً. لا يستورد Streamlit إطلاقاً (يعمل headless).

تشغيل يدوي (طوارئ/أتمتة): ``python -m services.analysis_runner <ملف.xlsx>``
"""
from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from conf.constants import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger("analysis_runner")

# قابلة للحقن في الاختبارات (monkeypatch على مستوى الوحدة)
_PROGRESS_PATH = DATA_DIR / "analysis_progress.json"
_UPLOADS_DIR = DATA_DIR / "uploads"
_LOGS_DIR = DATA_DIR / "logs"
_HEARTBEAT_SEC = 20          # نبض الخيط أثناء المراحل الطويلة
_STALE_SEC = 180             # نبض أقدم من هذا ⇒ العملية ميتة (يسمح بإعادة تشغيل)
_DEFAULT_ESTIMATE_SEC = 3000 # تقدير أول تشغيل بارد (~50 دقيقة)؛ يُستبدل بمدة آخر تشغيل

_thread_lock = threading.Lock()
_active_process: Optional[subprocess.Popen] = None


def _cgroup_memory_bytes() -> Optional[int]:
    """ذاكرة cgroup الحية إن كانت المنصة تدعمها؛ الغياب لا يعطّل التحليل."""
    try:
        with open("/sys/fs/cgroup/memory.current", "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _spawn_flags() -> int:
    """أعلام إطلاق تمنع نافذة طرفية منبثقة على ويندوز؛ بلا تأثير على غير ويندوز."""
    if sys.platform.startswith("win"):
        return subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)
    return 0

# أسماء المراحل للواجهة (عربية جاهزة للعرض)
PHASE_LABELS = {
    "loading_catalog": "تحميل الكتالوج وفحص الجودة",
    "missing": "كشف المفقودات",
    "matching": "مطابقة المنافسين والتحليل السعري (الأطول)",
    "salvage": "إنقاذ المستبعدات القابلة للمطابقة",
    "post": "ذكاء ما بعد التحليل (شذوذ/تنبيهات/جودة)",
    "saving": "حفظ النتائج على القرص",
    "done": "اكتمل",
}


# ─────────────────────────── ملف التقدم (ذرّي) ───────────────────────────

def read_progress() -> Optional[dict[str, Any]]:
    """يقرأ ملف التقدم أو يعيد None (ملف تالف/غائب = لا تشغيل)."""
    try:
        if not os.path.exists(_PROGRESS_PATH):
            return None
        with open(_PROGRESS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_progress(fields: dict[str, Any]) -> None:
    """يدمج الحقول في ملف التقدم بكتابة ذرّية (tmp ثم استبدال) — قراءة آمنة دوماً."""
    try:
        current = read_progress() or {}
        current.update(fields)
        current["updated_at"] = time.time()
        os.makedirs(os.path.dirname(_PROGRESS_PATH), exist_ok=True)
        tmp = f"{_PROGRESS_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False)
        os.replace(tmp, _PROGRESS_PATH)
    except Exception:  # فشل التقدم لا يوقف التحليل أبداً
        logger.exception("تعذّرت كتابة ملف التقدم")


def is_running(progress: Optional[dict[str, Any]] = None) -> bool:
    """هل يوجد تحليل حي الآن؟ (running=True ونبض حديث — نبض قديم = خيط ميت)."""
    p = progress if progress is not None else read_progress()
    if not p or not p.get("running"):
        return False
    updated = float(p.get("updated_at") or 0)
    return (time.time() - updated) < _STALE_SEC


def elapsed_and_estimate(p: dict[str, Any]) -> tuple[float, float]:
    """(الثواني المنقضية، التقدير الكلي) لعرض شريط تقدم زمني صادق."""
    started = float(p.get("started_at") or time.time())
    estimate = float(p.get("estimate_sec") or _DEFAULT_ESTIMATE_SEC)
    return max(0.0, time.time() - started), max(60.0, estimate)


# ─────────────────────────── الإطلاق ───────────────────────────

def start_background_analysis(file_bytes: bytes, filename: str) -> tuple[bool, str]:
    """يحفظ نسخة الملف المرفوع ويطلق التحليل في عملية نظام تشغيل منفصلة تماماً.

    عملية منفصلة (لا خيط) كي لا يتشارك حسابٌ طويل الـGIL مع خادم الويب مهما
    طال — هذا تحديداً ما سبّب تجمّد الخادم (Connection timed out) عبر خيط سابق.
    يعيد (نجح؟، رسالة عربية للمالك). الرفض الوحيد: تحليل آخر حي فعلاً.
    """
    global _active_process
    with _thread_lock:
        if is_running():
            return False, "يوجد تحليل يعمل الآن — انتظر اكتماله (شريط التقدم أدناه)."
        if _active_process is not None and _active_process.poll() is None:
            return False, "يوجد تحليل يعمل الآن في هذا الخادم — انتظر اكتماله."

        # نسخة قرصية من الرفع: تعيد التشغيل بلا إعادة رفع، وتحرر العملية الابنة من كائن الجلسة
        os.makedirs(_UPLOADS_DIR, exist_ok=True)
        safe_ext = os.path.splitext(filename or "")[1].lower() or ".xlsx"
        upload_path = str(_UPLOADS_DIR / f"last_catalog{safe_ext}")
        try:
            tmp = f"{upload_path}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(file_bytes)
            os.replace(tmp, upload_path)
        except Exception as exc:
            return False, f"تعذّر حفظ نسخة الملف المرفوع: {exc}"

        run_id = f"run_{int(time.time())}"
        prev = read_progress() or {}
        # تقدير المدة من آخر تشغيل *بارد* حصراً (لا كاش) — تشغيل سريع بكاش لا
        # يصلح تقديراً لتشغيل بارد لاحق (هذا بالضبط ما أنتج "~2 دقيقة متوقعة"
        # الخاطئة أثناء تشغيل بارد استغرق ساعات فعلياً).
        estimate = float(prev.get("cold_duration_sec") or 0) or _DEFAULT_ESTIMATE_SEC
        # تصفير صريحة لحقول التشغيل السابق (finished_at/summary/…) كي لا تتسرّب
        # قيم قديمة إلى سجل التشغيل الجديد عبر دمج _write_progress.
        os.makedirs(os.path.dirname(_PROGRESS_PATH), exist_ok=True)
        try:
            tmp = f"{_PROGRESS_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({
                    "run_id": run_id,
                    "running": True,
                    "done": False,
                    "error": None,
                    "phase": "loading_catalog",
                    "filename": filename,
                    "upload_path": upload_path,
                    "started_at": time.time(),
                    "updated_at": time.time(),
                    "estimate_sec": estimate,
                    "cold_duration_sec": prev.get("cold_duration_sec"),
                    "summary": None,
                }, fh, ensure_ascii=False)
            os.replace(tmp, _PROGRESS_PATH)
        except Exception:
            logger.exception("تعذّرت كتابة ملف التقدم عند بدء التشغيل")

        os.makedirs(_LOGS_DIR, exist_ok=True)
        log_path = _LOGS_DIR / f"analysis_child_{run_id}.log"
        argv = [
            sys.executable, "-m", "services.analysis_runner",
            "--child", upload_path, filename, run_id,
        ]
        try:
            with open(log_path, "wb") as log_fh:
                _active_process = subprocess.Popen(
                    argv,
                    cwd=str(PROJECT_ROOT),
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=_spawn_flags(),
                    close_fds=True,
                )
        except Exception as exc:
            _write_progress({"running": False, "done": True, "error": f"تعذّر إطلاق العملية: {exc}"})
            return False, f"تعذّر إطلاق عملية التحليل: {exc}"
        return True, "انطلق التحليل في عملية منفصلة — مقاوم لتحديث الصفحة وإغلاق التبويب وحتى إعادة تشغيل الخادم."


# ─────────────────────────── جسم التحليل (بلا Streamlit) ───────────────────────────

def _analysis_job(upload_path: str, filename: str, run_id: str) -> None:
    """يشغّل خط التحليل الكامل خدماتياً ويحفظ اللقطة بنفسه. كل فشل يُدوَّن ولا يُبتلع."""
    t_start = time.time()
    stop_beat = threading.Event()

    def _memory_sample(stage: str, **metadata: Any) -> None:
        """يحفظ عيّنة ذاكرة محدودة للتشخيص؛ لا يغيّر التحليل ولا يحتفظ بالنتائج."""
        sample: dict[str, Any] = {
            "stage": str(stage),
            "elapsed_sec": round(time.time() - t_start, 1),
            "cgroup_memory_bytes": _cgroup_memory_bytes(),
            **metadata,
        }
        previous = read_progress() or {}
        samples = previous.get("matching_memory_samples")
        history = list(samples) if isinstance(samples, list) else []
        history.append(sample)
        # حد ثابت: تكفي عينة البداية + كل 500 منتج + الذروات النهائية.
        _write_progress({
            "matching_memory_latest": sample,
            "matching_memory_samples": history[-64:],
        })

    def _beat() -> None:
        while not stop_beat.wait(_HEARTBEAT_SEC):
            _write_progress({})  # يلمس updated_at فقط (نبض حياة)

    beat_thread = threading.Thread(target=_beat, name="analysis-heartbeat", daemon=True)
    beat_thread.start()

    # ── ساعة الأطوار: تختم الطور المنقضي عند كل انتقال ────────────────────
    # لماذا: زمن التحليل الكلي مسجَّل منذ زمن (110.7 دقيقة)، لكن **توزيعه على
    # الأطوار لم يكن مسجَّلاً قط** — فكل تشخيص للبطء كان استقراءً من عيّنات
    # (قياس 2026-07-25: بناء الفهرس ~27% والمطابقة 2.6%، كلاهما استقراء لا
    # قياس جولة). هذه الساعة تجعل الجولة الطبيعية القادمة تقيس نفسها، فلا
    # نحتاج جولة إضافية بـ110 دقيقة على جهاز مضغوط الذاكرة لنعرف أين الزمن.
    # لا تغيّر ترتيب طور ولا منطقه — تسجيل زمن فقط.
    _timings: dict[str, float] = {}
    _cur = ["boot", time.time()]        # ما قبل أول طور = تهيئة (استيراد + حاوية)

    def _phase(name: str, **extra: Any) -> None:
        now = time.time()
        _timings[_cur[0]] = round(now - _cur[1], 1)
        _cur[0], _cur[1] = name, now
        _write_progress({"phase": name, "phase_timings": dict(_timings), **extra})

    try:
        from bootstrap import (
            build_container,
            populate_our_catalog,
            run_missing_analysis,
            run_pricing_analysis,
            run_v4_post_analysis,
        )
        from services.brand_manager import save_store_brands
        from services.catalog_service import load_catalog_bytes
        from ui.reconcile import reconcile_state
        from ui.state_manager import AppState

        container = build_container()

        _phase("loading_catalog")
        with open(upload_path, "rb") as fh:
            data = fh.read()
        our_df = load_catalog_bytes(data, filename)
        save_store_brands(our_df)          # مرجع فحص الماركات = ماركاتنا الحقيقية
        populate_our_catalog(our_df)       # مرجع مطابق الملكية «لديك/ليس لديك»

        _phase("missing", catalog_rows=int(len(our_df)))
        missing_df, mstats = run_missing_analysis(container, our_df)

        # تكافؤ المسارين: القصّ الصامت لحارس الذاكرة يُبلَّغ من هنا أيضاً، لا من
        # app.py وحده — هذا المشغّل هو المسار الفعلي لكل تحليل طويل.
        _phase(
            "matching", missing_rows=int(len(missing_df)),
            missing_capped=bool(mstats.get("capped")),
            missing_cap_limit=int(mstats.get("cap_limit", 0) or 0),
            matching_memory_samples=[],
            matching_memory_latest=None,
        )
        _memory_sample(
            "before_pricing_analysis",
            catalog_rows=int(len(our_df)),
            missing_rows=int(len(missing_df)),
        )
        # لا تحتاج المطابقة المفقودات حتى تعود نتائجها إلى bootstrap للتنقية.
        # تفريغها في ملف مؤقت يقلل تداخلها مع كامل كتالوج المنافسين ولا يغيرها.
        os.makedirs(_LOGS_DIR, exist_ok=True)
        missing_spill_path = _LOGS_DIR / f"missing_spill_{run_id}.pkl"
        missing_df.to_pickle(missing_spill_path)
        missing_rows = int(len(missing_df))
        del missing_df
        gc.collect()
        _memory_sample("after_missing_spill_release", missing_rows=missing_rows)
        try:
            sections, result, missing_clean, astats = run_pricing_analysis(
                container,
                our_df,
                missing_df=None,
                missing_spill_path=str(missing_spill_path),
                memory_callback=_memory_sample,
            )
        finally:
            try:
                missing_spill_path.unlink()
            except FileNotFoundError:
                pass
        _memory_sample(
            "after_pricing_analysis",
            result_total=int(getattr(result, "total", 0) or 0),
        )
        _write_progress({
            "matching_subphases_sec": dict(astats.get("matching_subphases_sec") or {}),
        })

        # ── 🔎 إنقاذ حتمي: كان يجري في المسار المتزامن (app.py) وحده ──
        # وهذا المشغّل هو المسار الفعلي لكل تحليل طويل، فكانت كل نتائجه تُحفَظ
        # بلا إنقاذ: مستبعدات لها نظير قوي تبقى مكانها. قياس 2026-07-25 على
        # نتائج آخر تحليل: 233 منتجاً بقيت مستبعدة بسبب هذه الفجوة وحدها.
        _phase("salvage")
        try:
            from services.deterministic_salvage import apply_deterministic_salvage

            n_salvaged = apply_deterministic_salvage(sections, result=result)
            if n_salvaged:
                astats["deterministic_salvaged"] = n_salvaged
        except Exception as _exc:  # noqa: BLE001 — محروس: فشله لا يكسر التحليل
            astats["salvage_error"] = str(_exc)[:200]

        _phase("post")
        try:
            result.duration_sec = round(time.time() - t_start, 1)
        except Exception:
            pass

        # حالة قرصية مستقلة عن الجلسة: نسترد لقطة أمس أولاً كي نحافظ على
        # hidden_products/processed_* (قرارات المالك) ثم نبدّل نتائج التحليل فقط.
        state = AppState()
        state.restore_results()
        state.our_catalog = our_df
        state.sections = sections
        state.missing_df = missing_clean
        state.analysis_results = result
        reconcile_state(state)
        run_v4_post_analysis(container, sections, result)

        _phase("saving")
        snapshot_saved = bool(state.persist_results())

        rec = getattr(result, "reconciliation", None)
        summary = {
            "counts": {
                str(getattr(k, "value", k)): int(v)
                for k, v in (result.section_counts or {}).items()
            },
            "total": int(getattr(result, "total", 0) or 0),
            "missing_confirmed": int(getattr(result, "missing_count", 0) or 0),
            "gap": int(getattr(rec, "gap", 0) or 0) if rec is not None else None,
            "duplicates": int(getattr(rec, "duplicate_count", 0) or 0) if rec is not None else None,
            "balanced": bool(getattr(rec, "is_balanced", False)) if rec is not None else None,
            "catalog_recovered": int(astats.get("catalog_recovered", 0) or 0),
            "deterministic_salvaged": int(astats.get("deterministic_salvaged", 0) or 0),
            "salvage_error": astats.get("salvage_error"),
            # عدّادات كانت تُحسَب في audit_stats ولا تُقرأ في أي مكان: انهيار كاشف
            # التسرّب (closed_loop_error) وعدد الصفوف المُسقَطة فعلاً (dropped_none).
            # تمريرها في الملخّص هو ما يجعلها تصل لوحة التحكم أصلاً.
            "closed_loop": astats.get("closed_loop"),
            "closed_loop_error": astats.get("closed_loop_error"),
            "dropped_none": int(astats.get("dropped_none", 0) or 0),
            "cached_pricing": bool(astats.get("cached", False)),
            "snapshot_saved": snapshot_saved,
        }
        total_dur = round(time.time() - t_start, 1)
        # ختم الطور الأخير («saving») — بلا هذا يبقى بلا زمن مسجَّل.
        _timings[_cur[0]] = round(time.time() - _cur[1], 1)
        final_fields: dict[str, Any] = {
            "running": False,
            "done": True,
            "error": None,
            "phase": "done",
            "duration_sec": total_dur,
            "finished_at": time.time(),
            "summary": summary,
            "phase_timings": dict(_timings),
        }
        # تقدير التشغيل القادم يُبنى من تشغيل *بارد* فقط — تشغيل مخدوم بالكاش
        # لا يمثّل زمن مطابقة حقيقي (هذا الفرق تحديداً كان سبب ETA خاطئ سابقاً).
        if not summary["cached_pricing"]:
            final_fields["cold_duration_sec"] = total_dur
        _write_progress(final_fields)
        logger.info("اكتمل التحليل الخلفي %s في %.1f ثانية (حفظ اللقطة=%s)",
                    run_id, time.time() - t_start, snapshot_saved)
    except Exception as exc:
        logger.exception("فشل التحليل الخلفي %s", run_id)
        _write_progress({
            "running": False,
            "done": True,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_sec": round(time.time() - t_start, 1),
            "finished_at": time.time(),
        })
    finally:
        stop_beat.set()


# ─────────────────────────── تشغيل يدوي/أتمتة ───────────────────────────

def run_sync_from_path(path: str, filename: Optional[str] = None) -> dict[str, Any]:
    """يشغّل التحليل متزامناً من ملف على القرص (طوارئ/أتمتة/تشغيل يدوي) ويعيد ملف التقدم النهائي.

    يهيّئ ملف التقدم بنفسه (خلافاً لوضع ``--child`` حيث الأب هيّأه مسبقاً).
    """
    name = filename or os.path.basename(path)
    run_id = f"cli_{int(time.time())}"
    prev = read_progress() or {}
    _write_progress({
        "run_id": run_id, "running": True, "done": False, "error": None,
        "phase": "loading_catalog", "filename": name, "upload_path": path,
        "started_at": time.time(),
        "estimate_sec": float(prev.get("cold_duration_sec") or 0) or _DEFAULT_ESTIMATE_SEC,
        "summary": None,
    })
    _analysis_job(path, name, run_id)
    return read_progress() or {}


if __name__ == "__main__":  # pragma: no cover — مدخل طوارئ يدوي/عملية ابنة
    argv = sys.argv[1:]
    if argv and argv[0] == "--child":
        # أُطلقت من start_background_analysis — الأب هيّأ ملف التقدم مسبقاً؛
        # لا نعيد كتابته هنا كي لا نفقد started_at/estimate_sec الحقيقيَّين.
        if len(argv) != 4:
            raise SystemExit(
                "usage: python -m services.analysis_runner --child <path> <filename> <run_id>",
            )
        _, child_path, child_name, child_run_id = argv
        _analysis_job(child_path, child_name, child_run_id)
    else:
        if not argv:
            raise SystemExit("usage: python -m services.analysis_runner <catalog.xlsx>")
        final = run_sync_from_path(argv[0])
        try:
            print(json.dumps(final.get("summary") or {"error": final.get("error")},
                             ensure_ascii=False, indent=2))
        except Exception:
            print("done. error=", final.get("error"))
