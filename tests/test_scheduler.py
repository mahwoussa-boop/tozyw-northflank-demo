"""tests/test_scheduler.py — اختبارات مجدوِل المهام الخلفية.

يغطي 12 اختبار وحدة لجميع عمليات ``TaskScheduler``:
تسجيل، استعلام، تمكين، تعطيل، حذف، تشغيل، حالة.
"""
from __future__ import annotations

import time

import pytest

from services.background.scheduler import TaskScheduler


# ── دوال مساعدة للاختبار ───────────────────────────────────────

def _dummy_job():
    """مهمة وهمية تنجح دائماً."""
    pass


def _slow_job():
    """مهمة وهمية تستغرق وقتاً قصيراً."""
    time.sleep(0.01)


def _failing_job():
    """مهمة وهمية تفشل دائماً."""
    raise ValueError("خطأ مقصود")


# ════════════════════════════════════════════════════════════════════
#  الاختبارات
# ════════════════════════════════════════════════════════════════════

class TestTaskScheduler:
    """اختبارات وحدة لـ TaskScheduler."""

    def test_register_job_returns_job_id(self):
        """تسجيل مهمة يُعيد معرّفاً فريداً."""
        sched = TaskScheduler()
        job_id = sched.register_job("test_job", _dummy_job)
        assert isinstance(job_id, str)
        assert len(job_id) == 12

    def test_get_jobs_returns_all_jobs(self):
        """get_jobs يُعيد جميع المهام المسجّلة."""
        sched = TaskScheduler()
        sched.register_job("job1", _dummy_job)
        sched.register_job("job2", _dummy_job)
        jobs = sched.get_jobs()
        assert len(jobs) == 2
        assert jobs[0]["name"] == "job1"
        assert jobs[1]["name"] == "job2"

    def test_get_jobs_excludes_func_reference(self):
        """get_jobs لا يكشف مرجع الدالة."""
        sched = TaskScheduler()
        sched.register_job("job1", _dummy_job)
        jobs = sched.get_jobs()
        assert "func" not in jobs[0]

    def test_enable_job_works(self):
        """enable_job يفعّل مهمة معطّلة."""
        sched = TaskScheduler()
        jid = sched.register_job("job1", _dummy_job, enabled=False)
        assert sched.enable_job(jid) is True
        jobs = sched.get_jobs()
        assert jobs[0]["enabled"] is True

    def test_disable_job_works(self):
        """disable_job يعطّل مهمة مفعّلة."""
        sched = TaskScheduler()
        jid = sched.register_job("job1", _dummy_job, enabled=True)
        assert sched.disable_job(jid) is True
        jobs = sched.get_jobs()
        assert jobs[0]["enabled"] is False

    def test_enable_nonexistent_returns_false(self):
        """enable_job لمهمة غير موجودة يُعيد False."""
        sched = TaskScheduler()
        assert sched.enable_job("nonexistent") is False

    def test_remove_job_removes(self):
        """remove_job يحذف المهمة من السجل."""
        sched = TaskScheduler()
        jid = sched.register_job("job1", _dummy_job)
        assert sched.remove_job(jid) is True
        assert len(sched.get_jobs()) == 0

    def test_remove_nonexistent_returns_false(self):
        """remove_job لمهمة غير موجودة يُعيد False."""
        sched = TaskScheduler()
        assert sched.remove_job("nonexistent") is False

    def test_run_job_executes_function(self):
        """run_job يشغّل الدالة وينجح."""
        sched = TaskScheduler()
        jid = sched.register_job("job1", _dummy_job)
        result = sched.run_job(jid)
        assert result["status"] == "completed"
        assert result["job_id"] == jid
        assert result["name"] == "job1"
        assert result["error"] == ""

    def test_run_job_returns_duration(self):
        """run_job يحسب مدة التشغيل بالمللي ثانية."""
        sched = TaskScheduler()
        jid = sched.register_job("slow", _slow_job)
        result = sched.run_job(jid)
        assert result["status"] == "completed"
        assert result["duration_ms"] >= 0

    def test_run_job_with_failing_function_returns_error(self):
        """run_job لمهمة فاشلة يُعيد حالة failed مع رسالة الخطأ."""
        sched = TaskScheduler()
        jid = sched.register_job("fail", _failing_job)
        result = sched.run_job(jid)
        assert result["status"] == "failed"
        assert "خطأ مقصود" in result["error"]
        assert result["duration_ms"] >= 0

    def test_run_job_not_found(self):
        """run_job لمهمة غير موجودة يُعيد not_found."""
        sched = TaskScheduler()
        result = sched.run_job("nonexistent")
        assert result["status"] == "not_found"

    def test_get_status_returns_counts(self):
        """get_status يُعيد إحصائيات صحيحة."""
        sched = TaskScheduler()
        sched.register_job("a", _dummy_job, enabled=True)
        sched.register_job("b", _dummy_job, enabled=True)
        sched.register_job("c", _dummy_job, enabled=False)
        status = sched.get_status()
        assert status["total_jobs"] == 3
        assert status["enabled_jobs"] == 2
        assert status["disabled_jobs"] == 1
        assert len(status["last_run_summary"]) == 3

    def test_register_multiple_jobs(self):
        """تسجيل عدة مهام — كل واحدة بمعرّف فريد."""
        sched = TaskScheduler()
        ids = [sched.register_job(f"job_{i}", _dummy_job) for i in range(5)]
        assert len(set(ids)) == 5  # جميعها فريدة
        assert len(sched.get_jobs()) == 5

    def test_run_job_updates_last_run(self):
        """run_job يُحدّث last_run بعد التشغيل الناجح."""
        sched = TaskScheduler()
        jid = sched.register_job("job1", _dummy_job)
        sched.run_job(jid)
        # الوصول المباشر للتحقق
        job = sched._find_job(jid)
        assert job is not None
        assert job["last_run"] is not None
