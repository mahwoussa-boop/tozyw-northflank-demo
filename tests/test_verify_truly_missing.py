"""tests/test_verify_truly_missing.py — بوابة تصدير سلة CSV بالبصمة.

تتحقّق أن المطابقة بالبصمة تستبعد المؤكَّد (match) فقط من «المفقودات»، فلا
تُخفي منتجاً جديداً محتملاً عن الملف (تفادي الإيجابيات الكاذبة).
"""
import pandas as pd

from utils.salla_shamel_export import verify_truly_missing


def test_excludes_only_confident_match() -> None:
    catalog = pd.DataFrame(
        [{"أسم المنتج": "ديور سوفاج او دو بارفيوم 100مل", "الماركة": "Dior"}]
    )
    missing = pd.DataFrame([
        {"منتج_المنافس": "ديور سوفاج EDP 100مل"},   # نفس المنتج بصيغة أخرى ⇒ match
        {"منتج_المنافس": "نيشان هاسيت بلوش 50مل"},   # مختلف تماماً ⇒ جديد
    ])
    truly, found = verify_truly_missing(missing, catalog)
    assert len(found) == 1
    assert len(truly) == 1
    assert "نيشان" in str(truly.iloc[0].to_dict())


def test_empty_catalog_keeps_all() -> None:
    missing = pd.DataFrame([{"منتج_المنافس": "أي منتج 100مل"}])
    truly, found = verify_truly_missing(missing, None)
    assert len(truly) == 1
    assert found.empty
