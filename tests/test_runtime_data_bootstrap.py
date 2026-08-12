"""حراسة مسار الإقلاع الدائم للنشر (runtime_data_bootstrap).

يحرس أربع حقائق أسقطت النشر فعلياً أو كادت:
1. الاستيراد يسبق البذرة — وإلا لا يقلع volume فارغ في صورة نحيفة.
2. الاستيراد يُنفَّذ مرة واحدة لكل إصدار (marker).
3. البيانات السابقة تُنقل إلى recovery_backups ولا تُحذف.
4. رابط استيراد فاشل لا يُسقط خدمة بياناتها صالحة أصلاً.
"""
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import runtime_data_bootstrap as boot


def _make_competitor_db(path: Path, rows: int = 3) -> None:
    """قاعدة منافسين صغيرة صالحة (نفس الجدول الذي يفحصه has_competitor_data)."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE competitor_products_store (competitor TEXT, name TEXT)")
    con.executemany(
        "INSERT INTO competitor_products_store VALUES (?, ?)",
        [(f"متجر{i}", f"منتج{i}") for i in range(rows)],
    )
    con.commit()
    con.close()


def _make_archive(tmp_path: Path, name: str, rows: int = 3) -> str:
    """أرشيف نتائج صالح الحد الأدنى، ويُعاد كرابط file:// صالح للاستيراد."""
    staging = tmp_path / f"src-{name}"
    staging.mkdir()
    _make_competitor_db(staging / "pricing_v18.db", rows)
    (staging / "pricing_cache.json").write_text(json.dumps({"df": name}), encoding="utf-8")
    (staging / "missing_cache.json").write_text(json.dumps({"df": name}), encoding="utf-8")

    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in staging.iterdir():
            zf.write(item, f"data/{item.name}")
    return archive.resolve().as_uri()


@pytest.fixture
def quiet_views(monkeypatch):
    """يعزل بناء العروض المشتقّة (pandas/streamlit) — تُختبر مستقلةً."""
    calls: list[Path] = []
    monkeypatch.setattr(boot, "sync_runtime_views", lambda d: calls.append(Path(d)))
    return calls


def test_has_competitor_data_rejects_unusable_databases(tmp_path):
    assert boot.has_competitor_data(tmp_path / "absent.db") is False

    empty = tmp_path / "empty.db"
    empty.touch()
    assert boot.has_competitor_data(empty) is False

    no_table = tmp_path / "no_table.db"
    sqlite3.connect(no_table).close()
    assert boot.has_competitor_data(no_table) is False

    no_rows = tmp_path / "no_rows.db"
    con = sqlite3.connect(no_rows)
    con.execute("CREATE TABLE competitor_products_store (competitor TEXT)")
    con.commit()
    con.close()
    assert boot.has_competitor_data(no_rows) is False

    good = tmp_path / "good.db"
    _make_competitor_db(good)
    assert boot.has_competitor_data(good) is True


def test_import_runs_before_seed_on_empty_volume(tmp_path, monkeypatch, quiet_views):
    """volume فارغ + صورة بلا بذرة: يجب أن يقلع بالاستيراد وحده.

    الترتيب السابق (البذرة أولاً) كان يرمي RuntimeError قبل بلوغ الاستيراد.
    """
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("TOZYW_SEED_DATA_DIR", raising=False)
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", _make_archive(tmp_path, "rev1"))
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev1")

    boot.main()

    assert boot.has_competitor_data(data_dir / "pricing_v18.db") is True
    assert (data_dir / "pricing_cache.json").exists()
    assert (data_dir / boot._IMPORT_MARKER).read_text(encoding="utf-8").strip() == "rev1"
    assert quiet_views == [data_dir]


def test_same_revision_is_imported_only_once(tmp_path, monkeypatch, quiet_views):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", _make_archive(tmp_path, "rev1"))
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev1")

    boot.main()
    first = (data_dir / "pricing_v18.db").stat().st_mtime_ns

    boot.main()  # إقلاع ثانٍ بنفس الإصدار
    assert (data_dir / "pricing_v18.db").stat().st_mtime_ns == first
    assert not list((data_dir / "recovery_backups").glob("*")) if (
        data_dir / "recovery_backups").exists() else True


def test_new_revision_preserves_previous_data(tmp_path, monkeypatch, quiet_views):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", _make_archive(tmp_path, "rev1", rows=3))
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev1")
    boot.main()

    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", _make_archive(tmp_path, "rev2", rows=9))
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev2")
    boot.main()

    con = sqlite3.connect(data_dir / "pricing_v18.db")
    assert con.execute("SELECT COUNT(*) FROM competitor_products_store").fetchone()[0] == 9
    con.close()

    backups = list((data_dir / "recovery_backups").iterdir())
    assert backups, "لم تُحفظ نسخة البيانات السابقة"
    preserved = [b for b in backups if (b / "pricing_v18.db").exists()]
    assert preserved, "قاعدة الإصدار السابق غير محفوظة"
    con = sqlite3.connect(preserved[0] / "pricing_v18.db")
    assert con.execute("SELECT COUNT(*) FROM competitor_products_store").fetchone()[0] == 3
    con.close()


def test_failed_import_keeps_serving_existing_data(tmp_path, monkeypatch, quiet_views, capsys):
    """رابط معطوب + بيانات صالحة موجودة ⇒ لا سقوط، والخدمة تُكمل."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", _make_archive(tmp_path, "rev1"))
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev1")
    boot.main()

    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", (tmp_path / "absent.zip").resolve().as_uri())
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev2")
    boot.main()  # يجب ألّا يرمي

    assert boot.has_competitor_data(data_dir / "pricing_v18.db") is True
    assert (data_dir / boot._IMPORT_MARKER).read_text(encoding="utf-8").strip() == "rev1"
    assert "فشل استيراد النتائج المطلوبة" in capsys.readouterr().err


def test_failed_import_without_any_data_raises(tmp_path, monkeypatch, quiet_views):
    """لا بيانات ولا استيراد ناجح ⇒ يجب أن يسقط بصوت عالٍ لا أن يعرض لوحة فارغة."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("TOZYW_SEED_DATA_DIR", raising=False)
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", (tmp_path / "absent.zip").resolve().as_uri())
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev1")

    with pytest.raises(RuntimeError, match="تعذّر تنزيل أرشيف النتائج"):
        boot.main()


def test_missing_url_and_no_data_names_the_env_vars(tmp_path, monkeypatch, quiet_views):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("TOZYW_SEED_DATA_DIR", raising=False)
    monkeypatch.delenv("TOZYW_RESULTS_IMPORT_URL", raising=False)
    monkeypatch.delenv("TOZYW_RESULTS_REVISION", raising=False)

    with pytest.raises(RuntimeError, match="TOZYW_RESULTS_IMPORT_URL"):
        boot.main()


def test_archive_without_competitor_rows_is_rejected(tmp_path, monkeypatch, quiet_views):
    """أرشيف فيه ملف SQLite لكن بلا صفوف: وجود الملف ليس نجاحاً."""
    staging = tmp_path / "hollow"
    staging.mkdir()
    con = sqlite3.connect(staging / "pricing_v18.db")
    con.execute("CREATE TABLE competitor_products_store (competitor TEXT)")
    con.commit()
    con.close()
    (staging / "pricing_cache.json").write_text("{}", encoding="utf-8")
    (staging / "missing_cache.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "hollow.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in staging.iterdir():
            zf.write(item, f"data/{item.name}")

    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("TOZYW_SEED_DATA_DIR", raising=False)
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", archive.resolve().as_uri())
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "hollow")

    with pytest.raises(RuntimeError, match="no valid competitor database"):
        boot.main()
    assert not (data_dir / "pricing_v18.db").exists(), "استُبدلت بيانات حيّة بأرشيف مرفوض"


def test_staging_stays_inside_data_dir(tmp_path, monkeypatch, quiet_views):
    """الـstaging على الـVolume لا على /tmp — الأرشيف يحتاج ~1.3غ.ب مفكوكاً."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOZYW_RESULTS_IMPORT_URL", _make_archive(tmp_path, "rev1"))
    monkeypatch.setenv("TOZYW_RESULTS_REVISION", "rev1")

    seen: list[Path] = []
    original = boot.stage_remote_archive

    def spy(url, stage_dir):
        seen.append(Path(stage_dir))
        return original(url, stage_dir)

    monkeypatch.setattr(boot, "stage_remote_archive", spy)
    boot.main()

    assert seen and seen[0].resolve().is_relative_to(data_dir.resolve())
    assert not list((data_dir / boot._STAGING_DIR).iterdir()), "بقايا staging لم تُنظَّف"


def test_assert_writable_reports_a_readable_reason(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    boot.assert_writable(data_dir)  # قابل للكتابة: لا يرمي
    assert not (data_dir / ".tozyw_write_probe").exists(), "ملف الفحص لم يُنظَّف"

    def deny(*_args, **_kwargs):
        raise PermissionError("read-only volume")

    monkeypatch.setattr(Path, "write_text", deny)
    with pytest.raises(RuntimeError, match="غير قابل للكتابة"):
        boot.assert_writable(data_dir)


def test_default_data_dir_matches_the_mounted_volume():
    """DATA_DIR الافتراضي يطابق نقطة تثبيت الـVolume في Northflank."""
    assert boot.DEFAULT_DATA_DIR == "/data"
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "DATA_DIR=/data" in dockerfile
    assert "/var/tozyw-demo" not in dockerfile
    assert "releases/download" not in dockerfile, "رابط أرشيف مدفون في الـDockerfile مجدداً"
