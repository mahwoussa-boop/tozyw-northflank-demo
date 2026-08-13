"""حراسة بقاء إعدادات المالك، وحراسة الإفصاح عن نقص اللقطة.

كلاهما فشلٌ صامت: الأول يضيع مع كل نشر لأن جذر التطبيق يُستبدل، والثاني يرسم
لوحةً **أصغر** بلا أثر يُشخَّص به.
"""
import importlib
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui.pages.settings as settings_page


def test_env_is_saved_on_the_volume_not_the_replaceable_app_root(tmp_path, monkeypatch):
    """‏/app يُستبدل مع كل نشر — الحفظ يجب أن يقع تحت DATA_DIR."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    assert settings_page._persistent_env_path() == data_dir / ".env"
    assert settings_page._save_env({"WEBHOOK_UPDATE_PRICES": "https://example.test/hook"})

    saved = (data_dir / ".env").read_text(encoding="utf-8")
    assert 'WEBHOOK_UPDATE_PRICES="https://example.test/hook"' in saved
    assert not settings_page._image_env_path().samefile(data_dir / ".env") \
        if settings_page._image_env_path().exists() else True


def test_saved_values_win_over_the_image_defaults(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    image_env = tmp_path / "image.env"
    image_env.write_text('MAKE_WEBHOOK_URL="from-image"\nOTHER="kept"\n', encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings_page, "_image_env_path", lambda: image_env)
    (data_dir / ".env").write_text('MAKE_WEBHOOK_URL="from-volume"\n', encoding="utf-8")

    env = settings_page._read_env()
    assert env["MAKE_WEBHOOK_URL"] == "from-volume", "قيمة الصورة طغت على المحفوظة"
    assert env["OTHER"] == "kept", "ضاعت قيمة لا يعرّفها الحجم"


def test_without_data_dir_behaviour_is_unchanged(tmp_path, monkeypatch):
    """التطوير المحلي بلا DATA_DIR يبقى كما كان — لا مسار جديد يُخترع."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert settings_page._persistent_env_path() == settings_page._image_env_path()


def test_boot_loads_the_persistent_env_without_beating_platform_secrets(
    tmp_path, monkeypatch,
):
    """‏conf.settings كان يحمّل جذر المستودع وحده، فالمحفوظ لا يُقرأ عند الإقلاع."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".env").write_text(
        'TOZYW_TEST_ONLY_A="from-volume"\nTOZYW_TEST_ONLY_B="from-volume"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOZYW_TEST_ONLY_B", "from-platform")
    monkeypatch.delenv("TOZYW_TEST_ONLY_A", raising=False)

    import conf.settings as settings_module
    settings_module._load_dotenv()

    import os
    assert os.environ["TOZYW_TEST_ONLY_A"] == "from-volume"
    assert os.environ["TOZYW_TEST_ONLY_B"] == "from-platform", \
        "‏.env طغى على سرّ المنصّة"


def test_missing_snapshot_frame_is_reported_not_swallowed(tmp_path, monkeypatch, caplog):
    """إطار مفقود يجب أن يترك أثراً — لوحة أصغر بصمت أخطر من فشل صريح."""
    import ui.state_manager as sm

    snapshot_dir = tmp_path / "ui_session"
    monkeypatch.setattr(sm, "_SNAPSHOT_PATH", tmp_path / "ui_session.json")

    state = sm.AppState(
        sections={
            "price_raise": pd.DataFrame({"المنتج": ["أ", "ب"]}),
            "excluded": pd.DataFrame({"المنتج": ["ج"]}),
        },
    )
    assert state.persist_results(full=True) is True

    frames = sorted(snapshot_dir.glob("*sections.excluded.json"))
    assert frames, "لم تُكتب إطارات اللقطة"
    frames[0].unlink()

    with caplog.at_level(logging.WARNING, logger="state_manager"):
        fresh = sm.AppState()
        restored = fresh.restore_results()

    assert restored is True, "الاستعادة الجزئية يجب أن تنجح لا أن تنهار"
    assert "لقطة ناقصة" in caplog.text
    assert "excluded" not in fresh.sections


def test_unreadable_snapshot_frame_is_reported(tmp_path, monkeypatch, caplog):
    import ui.state_manager as sm

    snapshot_dir = tmp_path / "ui_session"
    monkeypatch.setattr(sm, "_SNAPSHOT_PATH", tmp_path / "ui_session.json")
    state = sm.AppState(sections={"review": pd.DataFrame({"المنتج": ["أ"]})})
    assert state.persist_results(full=True) is True

    frames = sorted(snapshot_dir.glob("*sections.review.json"))
    frames[0].write_text("{ليس JSON", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="state_manager"):
        sm.AppState().restore_results()

    assert "لقطة ناقصة" in caplog.text


def test_state_manager_imports_logging_at_module_level():
    """الاستدعاءات الجديدة داخل ``try``؛ استيراد محلي مفقود = NameError يكسر اللوحة."""
    import ui.state_manager as sm
    assert getattr(sm, "logging", None) is not None
