"""tests/test_missing_service.py — اختبارات كشف المفقودات (P2).

تُحقن ``ClassifyKernel`` وهمية لاختبار خط الأنابيب دون قاعدة بيانات حيّة،
بينما تُستخدم ``MatchingService`` الحقيقية لقرار الملكية.
"""
import pandas as pd

from services.matching_service import MatchingService
from services.missing_service import (
    ClassifyKernel,
    MissingService,
    is_non_perfume,
    item_type,
    missing_signature,
)

FAKE = ClassifyKernel(
    classify_product=lambda n: "deodorant" if "مزيل" in n else "perfume",
    classify_category=lambda n: "fragrance",
    extract_size=lambda n: 100.0 if ("مل" in n or "100" in n or "150" in n) else 0.0,
    extract_brand=lambda n: "ماركة",
    is_sample=lambda n: False,
    is_tester=lambda n: "تستر" in n,
)


def test_filters_drop_reasons() -> None:
    assert is_non_perfume("مزيل عرق سبراي 150 مل", 40, FAKE) == (True, "class")
    assert is_non_perfume("طقم هدية مجموعة عطور 100 مل", 300, FAKE) == (True, "set")
    assert is_non_perfume("عطر فاخر 100 مل", 5, FAKE) == (True, "price")
    assert is_non_perfume("اب", 50, FAKE)[0] is True            # اسم قصير
    assert is_non_perfume("عطر فاخر بلا حجم هنا", 250, FAKE) == (True, "nosize")
    assert is_non_perfume("عطر فاخر اصلي 100 مل", 250, FAKE) == (False, "")


def test_item_type() -> None:
    assert item_type("عطر تستر 100 مل", FAKE) == "tester"
    assert item_type("عطر ريتيل 100 مل", FAKE) == "retail"


def test_signature_format(monkeypatch) -> None:
    """التوقيع يضمّ نسخة المنطق **والحارس** — أي تغيير فيهما يُبطل الكاش.

    بلا ذلك (قبل 2026-07-26) كان تغيير منطق الإنتاج أو ``MISSING_MAX_UNIQUE``
    يمرّ صامتاً: الكاش القديم يُعاد ويُبطل الإصلاح دون أثر مرئي.
    """
    monkeypatch.setenv("MISSING_MAX_UNIQUE", "400000")
    assert missing_signature(10000, 524288) == "F5v1|10000|524288|400000"
    # تغيير الحارس وحده يغيّر التوقيع ⇒ يُعاد الحساب
    monkeypatch.setenv("MISSING_MAX_UNIQUE", "250000")
    assert missing_signature(10000, 524288) == "F5v1|10000|524288|250000"
    # غيابه يعود للافتراضي المعلن (لا توقيع فارغ)
    monkeypatch.delenv("MISSING_MAX_UNIQUE", raising=False)
    assert missing_signature(10000, 524288).endswith("|250000")


def test_pipeline_owned_dropped_missing_kept() -> None:
    matching = MatchingService(["ديور سوفاج او دو تواليت 100 مل"])
    svc = MissingService(matching, classify_kernel=FAKE)
    candidates = [
        {"product_name": "ديور سوفاج او دو تواليت 100 مل", "min_price": 300},  # OWNED
        {"product_name": "عطر نادر جدا ليس لدينا ابدا 100 مل", "min_price": 250,
         "competitors_list": ["متجرX"], "competitor_count": 2,
         "image_url": "u", "suggested_price": 240, "brand": ""},               # MISSING
        {"product_name": "مزيل عرق سبراي 150 مل", "min_price": 40},            # class drop
        {"product_name": "طقم هدية مجموعة عطور 100 مل", "min_price": 300},     # set drop
    ]
    rows = svc.compute(candidates)
    assert len(rows) == 1
    row = rows[0]
    assert row["منتج_المنافس"] == "عطر نادر جدا ليس لدينا ابدا 100 مل"
    assert row["مستوى_الثقة"] == "green"        # مفقود مؤكد
    assert row["عدد_المنافسين"] == 2
    df = MissingService.to_dataframe(rows)
    assert isinstance(df, pd.DataFrame) and len(df) == 1


def test_first_seen_passes_through() -> None:
    """«تاريخ_أول_رصد» يمرّ من المرشّح إلى صف المفقود (لترتيب «الأحدث»)."""
    matching = MatchingService(["منتجنا الوحيد 100 مل"])
    svc = MissingService(matching, classify_kernel=FAKE)
    rows = svc.compute([
        {"product_name": "عطر نادر جدا ليس لدينا 100 مل", "min_price": 250,
         "competitor_count": 1, "first_seen_at": "2026-07-09 03:00:00"},
    ])
    assert len(rows) == 1
    assert rows[0]["تاريخ_أول_رصد"] == "2026-07-09 03:00:00"


def test_first_seen_absent_is_safe() -> None:
    """غياب «first_seen_at» في المرشّح ⇒ حقل فارغ آمن (لا كسر)."""
    matching = MatchingService(["منتجنا الوحيد 100 مل"])
    svc = MissingService(matching, classify_kernel=FAKE)
    rows = svc.compute([
        {"product_name": "عطر نادر اخر بلا تاريخ 100 مل", "min_price": 200,
         "competitor_count": 1},
    ])
    assert rows[0]["تاريخ_أول_رصد"] == ""
