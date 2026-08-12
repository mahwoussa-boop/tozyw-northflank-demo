"""services/background/scheduler.py — مجدوِل المهام الخلفية (بدون اعتماديات خارجية).

يوفّر سجل مهام خفيف الوزن يمكن توسيعه لاحقاً بـ APScheduler أو Celery:
- تسجيل المهام بأسماء وفترات زمنية.
- تشغيل يدوي (متزامن) لأي مهمة مسجّلة.
- تتبّع آخر تشغيل والتشغيل التالي.
- تمكين / تعطيل / حذف المهام.

⚠️ لا يستورد APScheduler ولا Celery ولا Streamlit.
"""
from __future__ import annotations

from core.product_entity import _utcnow

import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Optional


class TaskScheduler:
    """مجدوِل مهام خفيف الوزن — سجل مهام بلا اعتماديات خارجية.

    يخزّن المهام كقواميس ويشغّلها بشكل متزامن عند الطلب.
    صُمِّم ليكون قابلاً للتوسيع لاحقاً بـ APScheduler أو Celery
    دون تغيير الواجهة العامة.

    Args:
        container: مرجع اختياري لحاوية الاعتماديات (للاستخدام المستقبلي).
    """

    def __init__(self, container: Any = None) -> None:
        """يهيّئ المجدوِل مع مرجع اختياري لحاوية الاعتماديات.

        Args:
            container: حاوية الاعتماديات (اختيارية — للتمرير للمهام لاحقاً).
        """
        self._container = container
        self._jobs: list[dict[str, Any]] = []

    # ── تسجيل مهمة ────────────────────────────────────────────────

    def register_job(
        self,
        name: str,
        func: Callable[..., Any],
        *,
        interval_hours: float = 24,
        enabled: bool = True,
    ) -> str:
        """يسجّل مهمة جديدة في السجل.

        ينشئ معرّفاً فريداً للمهمة ويحسب وقت التشغيل التالي
        بناءً على الفترة الزمنية المحددة.

        Args:
            name: اسم المهمة (وصفي، بالعربية أو الإنجليزية).
            func: الدالة المراد تشغيلها.
            interval_hours: الفترة بين التشغيلات بالساعات (افتراضي: 24).
            enabled: هل المهمة مفعّلة؟ (افتراضي: نعم).

        Returns:
            معرّف المهمة (12 حرف hex).
        """
        job_id = uuid.uuid4().hex[:12]
        now = _utcnow()
        job: dict[str, Any] = {
            "job_id": job_id,
            "name": name,
            "func": func,
            "interval_hours": interval_hours,
            "enabled": enabled,
            "last_run": None,
            "next_run": now + timedelta(hours=interval_hours),
            "created_at": now,
        }
        self._jobs.append(job)
        return job_id

    # ── استعلامات ─────────────────────────────────────────────────

    def get_jobs(self) -> list[dict[str, Any]]:
        """يُعيد جميع المهام المسجّلة (بدون مرجع الدالة).

        يحذف مرجع ``func`` من النسخة المُعادة لتجنب مشاكل التسلسل
        (JSON serialization) ولأسباب أمنية.

        Returns:
            قائمة قواميس تصف المهام المسجّلة.
        """
        result: list[dict[str, Any]] = []
        for job in self._jobs:
            info = {k: v for k, v in job.items() if k != "func"}
            # تحويل datetime لسلاسل ISO
            for key in ("last_run", "next_run", "created_at"):
                val = info.get(key)
                if isinstance(val, datetime):
                    info[key] = val.isoformat()
            result.append(info)
        return result

    # ── تمكين / تعطيل / حذف ───────────────────────────────────────

    def _find_job(self, job_id: str) -> Optional[dict[str, Any]]:
        """يبحث عن مهمة بمعرّفها.

        Args:
            job_id: معرّف المهمة.

        Returns:
            قاموس المهمة أو None إن لم تُوجد.
        """
        for job in self._jobs:
            if job["job_id"] == job_id:
                return job
        return None

    def enable_job(self, job_id: str) -> bool:
        """يفعّل مهمة مُعطَّلة.

        Args:
            job_id: معرّف المهمة.

        Returns:
            True إذا تم التفعيل بنجاح، False إذا لم تُوجد المهمة.
        """
        job = self._find_job(job_id)
        if job is None:
            return False
        job["enabled"] = True
        return True

    def disable_job(self, job_id: str) -> bool:
        """يعطّل مهمة مفعّلة.

        Args:
            job_id: معرّف المهمة.

        Returns:
            True إذا تم التعطيل بنجاح، False إذا لم تُوجد المهمة.
        """
        job = self._find_job(job_id)
        if job is None:
            return False
        job["enabled"] = False
        return True

    def remove_job(self, job_id: str) -> bool:
        """يحذف مهمة من السجل نهائياً.

        Args:
            job_id: معرّف المهمة.

        Returns:
            True إذا تم الحذف بنجاح، False إذا لم تُوجد المهمة.
        """
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j["job_id"] != job_id]
        return len(self._jobs) < before

    # ── تشغيل يدوي ────────────────────────────────────────────────

    def run_job(self, job_id: str) -> dict[str, Any]:
        """يشغّل مهمة مسجّلة بشكل متزامن ويسجّل النتيجة.

        يقيس مدة التشغيل ويُحدّث ``last_run`` و ``next_run``.
        إذا فشلت المهمة، يسجّل الخطأ في الحقل ``error``.

        Args:
            job_id: معرّف المهمة المراد تشغيلها.

        Returns:
            قاموس يحتوي:
            - job_id: معرّف المهمة.
            - name: اسم المهمة.
            - status: "completed" | "failed" | "not_found".
            - duration_ms: مدة التشغيل بالمللي ثانية.
            - error: رسالة الخطأ (فارغة عند النجاح).
        """
        job = self._find_job(job_id)
        if job is None:
            return {
                "job_id": job_id,
                "name": "",
                "status": "not_found",
                "duration_ms": 0,
                "error": f"المهمة {job_id} غير موجودة",
            }

        start_time = time.monotonic()
        try:
            job["func"]()
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            now = _utcnow()
            job["last_run"] = now
            job["next_run"] = now + timedelta(hours=job["interval_hours"])
            return {
                "job_id": job_id,
                "name": job["name"],
                "status": "completed",
                "duration_ms": elapsed_ms,
                "error": "",
            }
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "job_id": job_id,
                "name": job["name"],
                "status": "failed",
                "duration_ms": elapsed_ms,
                "error": str(exc),
            }

    # ── حالة المجدوِل ─────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """يُعيد ملخصاً لحالة المجدوِل.

        Returns:
            قاموس يحتوي:
            - total_jobs: إجمالي المهام المسجّلة.
            - enabled_jobs: عدد المهام المفعّلة.
            - disabled_jobs: عدد المهام المعطّلة.
            - last_run_summary: ملخّص آخر تشغيل لكل مهمة.
        """
        total = len(self._jobs)
        enabled = sum(1 for j in self._jobs if j["enabled"])
        disabled = total - enabled

        last_run_summary: list[dict[str, Any]] = []
        for job in self._jobs:
            lr = job.get("last_run")
            last_run_summary.append({
                "job_id": job["job_id"],
                "name": job["name"],
                "last_run": lr.isoformat() if isinstance(lr, datetime) else lr,
                "enabled": job["enabled"],
            })

        return {
            "total_jobs": total,
            "enabled_jobs": enabled,
            "disabled_jobs": disabled,
            "last_run_summary": last_run_summary,
        }
