"""اختبارات مسار تشغيل تشخيص مزوّدي الذكاء الاصطناعي في بيئة النشر."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_config_from_data_dir(monkeypatch, data_dir: Path, *, platform_key: str = ""):
    """يعيد تحميل config بعد عزل أسرار الاختبار عن العملية الحالية."""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    for key in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "OPENROUTER_API_KEY", "COHERE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    if platform_key:
        monkeypatch.setenv("GEMINI_API_KEY", platform_key)
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_runtime_requirements_include_ai_engine_html_dependency():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "beautifulsoup4==4.15.0" in requirements


def test_config_loads_provider_key_saved_on_persistent_volume(monkeypatch, tmp_path):
    persisted_key = "persistent-key-abcdefghijklmnopqrstuvwxyz"
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={persisted_key}\n", encoding="utf-8")

    config = _load_config_from_data_dir(monkeypatch, tmp_path)
    try:
        assert config.GEMINI_API_KEYS == [persisted_key]
    finally:
        sys.modules.pop("config", None)


def test_platform_secret_keeps_priority_over_persistent_volume(monkeypatch, tmp_path):
    persisted_key = "persistent-key-abcdefghijklmnopqrstuvwxyz"
    platform_key = "platform-key-abcdefghijklmnopqrstuvwxyz"
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={persisted_key}\n", encoding="utf-8")

    config = _load_config_from_data_dir(monkeypatch, tmp_path, platform_key=platform_key)
    try:
        assert config.GEMINI_API_KEYS == [platform_key]
    finally:
        sys.modules.pop("config", None)
