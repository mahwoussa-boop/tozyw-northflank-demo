# -*- coding: utf-8 -*-
"""تدوير data/backups/ (قرار المالك 2026-07-22: آخر 7 نسخ).

يتحقّق أن rotate_backups تحذف فقط أقدم المجلدات المؤرَّخة الزائدة عن الحدّ،
تُبقي الأحدث، ولا تمسّ ملفات متفرقة خارج نمط التاريخ.
"""
from pathlib import Path

from scripts.rotate_backups import rotate_backups


def _make_dated_dirs(root: Path, names: list[str]) -> None:
    for n in names:
        (root / n).mkdir(parents=True)


def test_rotate_deletes_oldest_beyond_keep(tmp_path):
    names = [f"2026-07-{d:02d}" for d in range(1, 11)]  # 10 مجلدات مؤرَّخة
    _make_dated_dirs(tmp_path, names)
    deleted = rotate_backups(str(tmp_path), keep=7)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert len(remaining) == 7
    assert remaining == names[-7:]  # الأحدث 7 بقيت
    assert sorted(deleted) == names[:3]  # الأقدم 3 حُذفت


def test_rotate_ignores_non_dated_files_and_dirs(tmp_path):
    _make_dated_dirs(tmp_path, [f"2026-07-{d:02d}" for d in range(1, 4)])
    (tmp_path / "pricing_v18_pre_avail_20260704.db").write_bytes(b"x")  # لا يطابق نمط التاريخ
    (tmp_path / "random_folder").mkdir()  # لا يطابق نمط التاريخ

    deleted = rotate_backups(str(tmp_path), keep=1)

    assert (tmp_path / "pricing_v18_pre_avail_20260704.db").exists()
    assert (tmp_path / "random_folder").exists()
    assert len(deleted) == 2  # فقط اثنان من الثلاثة المؤرَّخة حُذفا (أبقى 1)


def test_rotate_noop_when_under_limit(tmp_path):
    _make_dated_dirs(tmp_path, ["2026-07-20", "2026-07-21"])
    assert rotate_backups(str(tmp_path), keep=7) == []
    assert len(list(tmp_path.iterdir())) == 2


def test_rotate_missing_dir_returns_empty(tmp_path):
    assert rotate_backups(str(tmp_path / "nope"), keep=7) == []
