"""اختبارات عرض فحص مزوّدي الذكاء الاصطناعي بأمان."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.pages.settings import _ai_diagnostic_rows


def test_diagnostic_rows_keep_status_but_never_include_raw_details_or_keys():
    report = {
        "gemini": [{
            "key": 2,
            "status": "✅ يعمل",
            "status_code": 200,
            "detail": "يجب ألا يظهر هذا النص أو أي مفتاح",
        }],
        "openrouter": "⚠️ مفتاح غير موجود",
        "cohere": "✅ يعمل (command-a-03-2025)",
        "recommendations": [],
    }

    rows = _ai_diagnostic_rows(report)

    assert rows == [
        {"المزوّد": "Gemini", "المفتاح": 2, "الحالة": "✅ يعمل", "HTTP": 200},
        {"المزوّد": "Openrouter", "المفتاح": "—", "الحالة": "⚠️ مفتاح غير موجود", "HTTP": "—"},
        {"المزوّد": "Cohere", "المفتاح": "—", "الحالة": "✅ يعمل (command-a-03-2025)", "HTTP": "—"},
    ]
    assert "يجب ألا يظهر" not in str(rows)


def test_diagnostic_rows_report_unconfigured_fallbacks_explicitly():
    rows = _ai_diagnostic_rows({"gemini": []})

    assert rows == [
        {"المزوّد": "Openrouter", "المفتاح": "—", "الحالة": "⚠️ غير مهيأ", "HTTP": "—"},
        {"المزوّد": "Cohere", "المفتاح": "—", "الحالة": "⚠️ غير مهيأ", "HTTP": "—"},
    ]
