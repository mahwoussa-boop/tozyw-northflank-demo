# -*- coding: utf-8 -*-
"""حكم فحص صحّة الأتمتة (scripts/report_automation_health.py).

الحالات المحورية مأخوذة من عطبين حقيقيين وقعا فعلاً على جهاز المتجر:
عطب 2026-07-23 (مسار إجراء مشوّه الترميز ⇒ ملف غير موجود) وعطب 2026-07-25
(``run_vision_verify.bat`` حُذف من القرص). كلاهما ظلّ صامتاً أياماً، فهذه
الاختبارات تحرس ألّا يعود الحكم للسكوت عنهما.
"""
from datetime import datetime, timedelta, timezone

from scripts.report_automation_health import (
    FAIL,
    OK,
    WARN,
    hours_since,
    judge_task,
    parse_ts,
)


def test_missing_action_file_is_a_failure():
    """عطب الكشط 2026-07-23: التعريف يشير لملف غير موجود ⇒ يجب أن يُكشف."""
    verdict = judge_task(
        "MahwousNightlyScrape", "Ready", 0,
        r"C:\...\ظ…ظ‡ظˆظˆط³\run_nightly_scrape.bat", False,
    )
    assert verdict["status"] == FAIL
    assert any("غير موجود" in line for line in verdict["lines"])


def test_exit_zero_but_no_effect_is_a_failure():
    """جوهر الأداة: رمز خروج 0x0 لا يكفي — غياب الأثر وحده يوجب الفشل."""
    verdict = judge_task(
        "MahwousNightlyScrape", "Ready", 0, r"C:\ok.bat", True,
        effect={"ok": False, "text": "جولات كشط خلال 24 ساعة: 0 متجر"},
    )
    assert verdict["status"] == FAIL


def test_healthy_task_with_effect_passes():
    verdict = judge_task(
        "MahwousDailyBackup", "Ready", 0, r"C:\ok.bat", True,
        effect={"ok": True, "text": "أحدث نسخة: 2026-07-25-daily"},
    )
    assert verdict["status"] == OK


def test_nonzero_exit_code_is_a_failure():
    verdict = judge_task("MahwousVisionVerify", "Ready", 1, r"C:\ok.bat", True)
    assert verdict["status"] == FAIL
    assert any("0x1" in line for line in verdict["lines"])


def test_running_and_never_run_codes_are_accepted():
    """0x41301 (قيد التشغيل) و0x41303 (لم تُشغَّل بعد) حالتان سليمتان لا فشل."""
    assert judge_task("MahwousWatchdog", "Running", 0x41301, r"C:\ok.bat", True)["status"] == OK
    assert judge_task("MahwousServer", "Ready", 0x41303, r"C:\ok.bat", True)["status"] == OK


def test_disabled_task_is_warning_not_failure():
    """قرار مالك 2026-07-25: مهمة معطَّلة مركونة — تنبيه كي لا تُغرق الأعطال الحقيقية."""
    verdict = judge_task("MahwousVisionVerify", "Disabled", 1, r"C:\gone.bat", False)
    assert verdict["status"] == WARN
    assert any("معطَّلة" in line for line in verdict["lines"])


def test_path_less_action_is_not_flagged():
    """إجراء يُحلّ عبر PATH (مثل 'powershell') يصل exec_exists=True ⇒ لا إيجابية كاذبة."""
    assert judge_task("MahwousWatchdog", "Running", 0x41301, "powershell", True)["status"] == OK


def test_parse_ts_handles_both_repo_timestamp_shapes():
    naive = parse_ts("2026-07-25 03:30:09")
    aware = parse_ts("2026-07-25T06:26:07.657292+00:00")
    assert naive is not None and naive.tzinfo is None
    assert aware is not None and aware.tzinfo is not None
    assert parse_ts(None) is None and parse_ts("نص ليس طابعاً") is None


def test_hours_since_uses_matching_clock_per_timestamp_kind():
    """الطابع الساذج يُقارن بالمحلّي والواعي بـUTC — خلطهما يعطي عمراً كاذباً بفارق المنطقة."""
    now_local = datetime(2026, 7, 25, 12, 0, 0)
    now_utc = datetime(2026, 7, 25, 9, 0, 0, tzinfo=timezone.utc)  # الرياض = UTC+3

    assert hours_since("2026-07-25 09:00:00", now_local, now_utc) == 3.0
    assert hours_since("2026-07-25T06:00:00+00:00", now_local, now_utc) == 3.0
    assert hours_since(None, now_local, now_utc) is None
    assert hours_since(now_local - timedelta(hours=2), now_local, now_utc) == 2.0
