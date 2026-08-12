"""توحيد بيت البيانات — constants والمحرك يقعان على قاعدة utils.data_paths نفسها.

بعد تقاعد بيت Desktop\\data (2026-07-04) صار get_data_dir() المصدرَ الوحيد
لمجلد البيانات؛ هذا الاختبار يمنع عودة أي حساب مسار مستقل (parents[2] القديم).
"""
from pathlib import Path

from conf import constants
from engines import competitor_intelligence
from utils.data_paths import get_data_db_path


def test_competitor_db_path_unified_with_data_paths() -> None:
    expected = Path(get_data_db_path("pricing_v18.db")).resolve()
    assert Path(constants.COMPETITOR_DB_PATH).resolve() == expected
    assert Path(competitor_intelligence.DB_PATH).resolve() == expected
