# -*- coding: utf-8 -*-
"""قياس زمن فتح الصفحات الظاهرة (وحدة هـ1 من docs/خطة-التطوير-الشاملة.md).

يُشغِّل كل صفحة من PAGES (باستثناء HIDDEN_PAGES) عبر AppTest داخل العملية —
نفس آلية المعاينة القائمة في tests/ — بثلاث محاولات معزولة لكلٍّ (نسخة AppTest
جديدة كل مرة)، ويطبع الوسيط بالمللي ثانية. يقرأ القاعدة الحيّة كما تفعل
اختبارات AppTest الأخرى في المستودع (لا عزل للقراءة، فقط للكتابة — تماماً
كما في tests/conftest.py). العتبة المتفق عليها في الخطة: ≤150ms.

الاستخدام: .venv/Scripts/python.exe scripts/measure_page_open_times.py
"""
from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest

from app import HIDDEN_PAGES, PAGES
from ui.state_manager import AppState

THRESHOLD_MS = 150.0
RUNS_PER_PAGE = 3


def _measure_page(page_name: str, tmp_dir: Path) -> list[float] | None:
    """يقيس صفحة واحدة RUNS_PER_PAGE مرة؛ يعيد None إن فشلت كل المحاولات."""
    times: list[float] = []
    last_exc = None
    for i in range(RUNS_PER_PAGE):
        try:
            import services.pricing_shadow as _ps
            import services.ai_service as _ais
            import ui.state_manager as _sm
            with patch.object(_ps, "_default_db", lambda: str(tmp_dir / f"shadow_{i}.db")), \
                 patch.object(_ais, "_default_ai_cache_db", lambda: str(tmp_dir / f"ai_cache_{i}.db")), \
                 patch.object(_sm, "_SNAPSHOT_PATH", tmp_dir / f"ui_session_{i}.json"):
                at = AppTest.from_file("app.py", default_timeout=180)
                at.session_state["_app_state_v2"] = AppState()
                at.session_state["nav_page"] = page_name
                start = time.perf_counter()
                at.run()
                elapsed_ms = (time.perf_counter() - start) * 1000
                if at.exception:
                    last_exc = list(at.exception)
                    continue
                times.append(elapsed_ms)
        except Exception as exc:  # noqa: BLE001 - نريد الاستمرار لبقية الصفحات
            last_exc = exc
    if not times:
        print(f"   ⚠️ فشلت كل المحاولات: {last_exc}")
        return None
    return times


def main() -> None:
    visible_pages = [name for name in PAGES if name not in HIDDEN_PAGES]
    print(f"قياس {len(visible_pages)} صفحة ظاهرة (من أصل {len(PAGES)}، {len(HIDDEN_PAGES)} مخفيّة)، "
          f"{RUNS_PER_PAGE} محاولات معزولة لكلٍّ...\n")

    results: dict[str, float | None] = {}
    with tempfile.TemporaryDirectory(prefix="mahwous_perf_") as tmp:
        tmp_dir = Path(tmp)
        for page_name in visible_pages:
            times = _measure_page(page_name, tmp_dir)
            if times is None:
                results[page_name] = None
                continue
            med = statistics.median(times)
            results[page_name] = med
            flag = "⛔" if med > THRESHOLD_MS else "✅"
            samples = ", ".join(f"{t:.0f}ms" for t in times)
            print(f"{flag} {page_name:<25} وسيط {med:>8.1f}ms   (عيّنات: {samples})")

    print("\n--- الخلاصة ---")
    over = {k: v for k, v in results.items() if v is not None and v > THRESHOLD_MS}
    failed = [k for k, v in results.items() if v is None]
    if over:
        print(f"تجاوز العتبة ({THRESHOLD_MS:.0f}ms): {len(over)} صفحة — {list(over.keys())}")
    else:
        print(f"لا صفحة تجاوزت العتبة ({THRESHOLD_MS:.0f}ms).")
    if failed:
        print(f"فشلت في القياس: {len(failed)} صفحة — {failed}")


if __name__ == "__main__":
    main()
