"""م8 — الكاشط وسكربت النسخ يحترمان متغيّر البيئة DATA_DIR.

كلاهما كان يُثبّت المسار على ``<المشروع>/data`` متجاوزاً ``DATA_DIR``:
  • الكاشط كان سيقرأ قائمة منافسين **أخرى** غير التي تكتبها بقية الوحدات.
  • سكربت النسخ كان سينسخ مجلداً **فارغاً** ويُبلّغ نجاحاً — نسخة أمان تكذب
    أخطر من غيابها.
كلاهما يرتدّ إلى المسار القديم نفسه عند غياب المتغيّر ⇒ لا تغيّر محلياً.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


def _reload_with_data_dir(module_name: str, path: str, monkeypatch):
    monkeypatch.setenv("DATA_DIR", path)
    import utils.data_paths as dp
    importlib.reload(dp)
    module = importlib.import_module(module_name)
    return importlib.reload(module)


@pytest.fixture(autouse=True)
def _restore_modules():
    """يُعيد الوحدات لحالتها الأصلية كي لا تُسمّم بقية الحزمة."""
    yield
    import utils.data_paths as dp
    os.environ.pop("DATA_DIR", None)
    importlib.reload(dp)
    for name in ("scripts.backup_data", "engines.mahally_scraper"):
        try:
            importlib.reload(importlib.import_module(name))
        except Exception:
            pass


def test_backup_script_targets_the_configured_data_dir(tmp_path, monkeypatch):
    target = tmp_path / "volume-data"
    target.mkdir()
    mod = _reload_with_data_dir("scripts.backup_data", str(target), monkeypatch)
    assert Path(mod.DATA_DIR).resolve() == target.resolve()


def test_backup_script_falls_back_to_project_data_when_unset(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    import utils.data_paths as dp
    importlib.reload(dp)
    mod = importlib.reload(importlib.import_module("scripts.backup_data"))
    assert Path(mod.DATA_DIR).name == "data"


def test_scraper_competitors_file_follows_the_configured_data_dir(tmp_path, monkeypatch):
    # الوحدة تحمي استيراد bs4/selenium داخلياً فتُستورَد بدونهما — لا تخطّي هنا.
    target = tmp_path / "volume-data"
    target.mkdir()
    mod = _reload_with_data_dir("engines.mahally_scraper", str(target), monkeypatch)
    assert Path(mod.COMPETITORS_JSON).parent.resolve() == target.resolve()
    assert Path(mod.COMPETITORS_JSON).name == "competitors_list_v30.json"
