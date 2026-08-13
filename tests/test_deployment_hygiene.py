# -*- coding: utf-8 -*-
"""حواجز صغيرة تمنع أخطاء تغليف ونفاذية واجهة سبق أن ظهرت في الإنتاج."""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _dockerignore_patterns() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _ignored(path: str, patterns: list[str]) -> bool:
    """يكفي لهذا الحارس نمط الإهمال البسيط المستعمل في `.dockerignore`."""
    return any(fnmatch(path, pattern) for pattern in patterns)


def test_dockerignore_excludes_sqlite_sidecars_without_the_database():
    """ملفات WAL/SHM لا قيمة لها في الصورة من دون قاعدة SQLite الأم.

    القاعدة نفسها مستبعدة أصلاً لأن مصدر الحقيقة هو Volume؛ لذلك يجب استبعاد
    جانبيها أيضاً كي لا تُشحن حالة SQLite قديمة أو مضللة إلى الصورة.
    """
    patterns = _dockerignore_patterns()
    for sidecar in (
        "data/pricing_v18.db-wal",
        "data/pricing_v18.db-shm",
        "data/cache.sqlite-wal",
        "data/cache.sqlite-shm",
        "data/catalog.sqlite3-wal",
        "data/catalog.sqlite3-shm",
    ):
        assert _ignored(sidecar, patterns), f"ملف SQLite جانبي غير مستبعد: {sidecar}"


def test_pagination_number_input_has_an_accessible_nonempty_label():
    """تبقى التسمية مخفية بصرياً، لكنها غير فارغة لتفادي تحذير Streamlit."""
    source = (ROOT / "ui" / "components" / "pagination.py").read_text(encoding="utf-8")
    assert 'c2.number_input(\n                "انتقل إلى الصفحة"' in source
    assert 'label_visibility="collapsed"' in source
