# -*- coding: utf-8 -*-
"""tests/test_missing_family_collapse.py — حارس «انهيار العائلة في عيّنة 1مل».

العطب (مقيس على القاعدة الحيّة 2026-07-26): ``miss_bare`` يُسقط الأرقام فتنهار
كل أحجام المنتج في اسم مجرّد واحد، و``_dedup`` يُبقي **الأرخص** — وهو غالباً
«عيّنة 1مل». وكان ``is_non_perfume`` يُطبَّق **بعد** الدمج فيُسقط ذلك الفائز
لأنه «ميني» ⇒ تختفي العائلة كلها ومعها الزجاجة الأصلية بمئات الريالات.

أثره الحقيقي: ``dior sauvage`` (149 منتجاً، 8–1,411 ر.س) · ``dior هوم`` (140) ·
``versace eros`` (120) · ``burberry هيرو`` (121) — كلها غائبة عن المفقودات رغم
أن المتجر لا يملكها.
"""
import pandas as pd
import pytest

from services.matching_service import Ownership
from services.missing_service import MissingService


class _FakeKernel:
    """نواة تطبيع مبسّطة تُحاكي سلوك النواة الحقيقية في ما يهمّ هذا الاختبار.

    الحقيقية تُسقط الحجم من الاسم المجرّد (``miss_bare`` يرمي الأرقام و«مل»
    ضمن الكلمات الشائعة)، فتنهار كل الأحجام في دلو واحد — وهو ما رُصد حيّاً:
    ``dior sauvage`` = 149 منتجاً بأحجام وأسعار مختلفة في اسم مجرّد واحد.
    """

    def normalize_name(self, text: str) -> str:
        import re
        s = str(text).lower().replace("-", " ")
        return re.sub(r"\d+(?:\.\d+)?\s*(?:مل|ml)\b", " ", s)

    def normalize(self, text: str) -> str:
        return str(text).lower()

    def extract_brand_fast(self, text: str) -> str:
        return ""

    def extract_size(self, text: str) -> float:
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*مل", str(text))
        return float(m.group(1)) if m else 0.0

    def extract_gender(self, text: str) -> str:
        return ""


class _FakeClassify:
    """نواة تصنيف: كل شيء عطر، والحجم يُستخرج من الاسم."""

    def classify_product(self, name: str) -> str:
        return "perfume"

    def classify_category(self, name: str) -> str:
        return "عطور"

    def extract_size(self, name: str) -> float:
        return _FakeKernel().extract_size(name)

    def extract_brand(self, name: str) -> str:
        return "ديور"

    def is_sample(self, name: str) -> bool:
        return "عينة" in str(name)

    def is_tester(self, name: str) -> bool:
        return "تستر" in str(name)


class _FakeMatching:
    """مُطابِق يعتبر كل شيء مفقوداً (نعزل سلوك الدمج/الفلترة وحده)."""

    kernel = _FakeKernel()

    def evaluate(self, name: str, brand: str = ""):
        class _Out:
            ownership = Ownership.MISSING
            score = 0.0
            our_match = None
            reason = ""
        return _Out()


def _cand(name: str, price: float) -> dict:
    return {
        "product_name": name, "brand": "ديور", "min_price": price,
        "suggested_price": max(0.0, price - 1), "competitors_list": "متجر أ",
        "competitor_count": 1, "category": "عطور", "image_url": "",
        "image_urls": "", "first_seen_at": "2026-07-01",
    }


@pytest.fixture()
def svc() -> MissingService:
    return MissingService(_FakeMatching(), classify_kernel=_FakeClassify())


def test_cheap_mini_does_not_erase_the_whole_family(svc):
    """عيّنة 1مل رخيصة يجب ألّا تسحب معها الزجاجة الكاملة إلى العدم."""
    candidates = [
        _cand("عينة ديور سوفاج بارفيوم 1مل", 8.0),      # ميني ⇒ يُسقَط
        _cand("عطر ديور سوفاج بارفيوم 100مل", 620.0),   # صالح ⇒ يجب أن يبقى
        _cand("عطر ديور سوفاج بارفيوم 200مل", 1411.0),  # صالح
    ]
    rows = svc.compute(candidates)
    assert rows, "العائلة اختفت بالكامل — عاد عطب انهيار العائلة"
    names = [r["منتج_المنافس"] for r in rows]
    assert not any("1مل" in n for n in names), "العيّنة الميني تسرّبت إلى المفقودات"
    # الأرخص **الصالح** هو الفائز بالدمج (لا الميني، ولا الأغلى)
    assert len(rows) == 1
    assert rows[0]["سعر_المنافس"] == 620.0


def test_family_of_only_minis_still_dropped(svc):
    """عائلة كلها ميني تبقى مُسقَطة — التصفية لم تُضعَف."""
    rows = svc.compute([
        _cand("عينة ديور سوفاج 1مل", 8.0),
        _cand("عينة ديور سوفاج 2مل", 12.0),
    ])
    assert rows == []


def test_valid_family_unaffected(svc):
    """عائلة بلا ميني: السلوك كما كان تماماً (الأرخص يفوز)."""
    rows = svc.compute([
        _cand("عطر ديور سوفاج 100مل", 620.0),
        _cand("عطر ديور سوفاج 200مل", 1411.0),
    ])
    assert len(rows) == 1
    assert rows[0]["سعر_المنافس"] == 620.0


def test_expensive_survives_when_it_is_the_only_valid_one(svc):
    """لو كان الصالح الوحيد هو الأغلى، فهو من يظهر — لا يُفقد بسبب رفيق رخيص."""
    rows = svc.compute([
        _cand("عينة ثمين دياديم مصغر 5مل", 138.0),   # ميني ⇒ يُسقَط
        _cand("عطر ثمين دياديم بيرفيوم 50مل", 540.5),
    ])
    assert len(rows) == 1
    assert rows[0]["سعر_المنافس"] == 540.5


def test_dataframe_shape_unchanged(svc):
    """مخطّط الإطار لم يتغيّر (الواجهة تعتمد أسماء الأعمدة حرفياً)."""
    rows = svc.compute([_cand("عطر ديور سوفاج 100مل", 620.0)])
    df = MissingService.to_dataframe(rows)
    assert isinstance(df, pd.DataFrame) and len(df) == 1
    for col in ("منتج_المنافس", "سعر_المنافس", "الماركة", "مستوى_الثقة"):
        assert col in df.columns
