"""conftest.py — يضيف جذر حزمة v2 إلى مسار الاستيراد كي تعمل الحزم العليا
(core / config / infrastructure / services / ui) أثناء pytest.
"""
import sys
from pathlib import Path

_V2_ROOT = Path(__file__).resolve().parents[1]
if str(_V2_ROOT) not in sys.path:
    sys.path.insert(0, str(_V2_ROOT))


# ─── تنظيف الاتصالات المفتوحة بعد كل اختبار ───
import pytest


@pytest.fixture(autouse=True)
def _close_global_db_conn():
    """يغلق الاتصال الدائم في engines/engine.py بعد كل اختبار (ResourceWarning)."""
    yield
    try:
        from engines import engine as _engine
        if hasattr(_engine, "_close_db_conn"):
            _engine._close_db_conn()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _shadow_db_isolated(monkeypatch, tmp_path):
    """يعزل السياج الظِّلّي عن pricing_v18.db الحقيقية في كل الاختبارات.
    اختبارات الظلّ نفسها تمرّر db_path صريحاً فلا تتأثر بهذا العزل."""
    try:
        import services.pricing_shadow as _ps
        monkeypatch.setattr(_ps, "_default_db",
                            lambda: str(tmp_path / "shadow_isolated.db"))
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _ai_cache_db_isolated(monkeypatch, tmp_path):
    """يعزل كاش AI (SqliteAICache) عن pricing_v18.db الحقيقية في كل الاختبارات.
    اختبارات الكاش نفسها تمرّر db_path صريحاً فلا تتأثر بهذا العزل."""
    try:
        import services.ai_service as _ais
        monkeypatch.setattr(_ais, "_default_ai_cache_db",
                            lambda: str(tmp_path / "ai_cache_isolated.db"))
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _snapshot_isolated(monkeypatch, tmp_path):
    """يعزل لقطة الجلسة عن ``data/ui_session.json`` الحقيقية في كل الاختبارات.

    حرجٌ: أي اختبار AppTest يضغط زرّاً يستدعي ``persist_results`` (مثل لوحة «قد
    تملكه باسم آخر») كان يكتب بيانات الاختبار فوق **نتائج تحليل المالك الحيّة**
    (عطل أُصلح 2026-07-10). هذا العزل يعيد توجيه الكتابة إلى مجلد مؤقت لكل اختبار."""
    try:
        import ui.state_manager as _sm
        monkeypatch.setattr(_sm, "_SNAPSHOT_PATH", tmp_path / "ui_session.json")
    except Exception:
        pass
