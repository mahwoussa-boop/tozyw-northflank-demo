"""tests/test_missing_queue_dedup.py — منع التكرار بالبصمة في طابور المفقودات.

يغطّي الضوابط المتفق عليها للخطوة ١:
  • المطابقة ضدّ كتالوج المتجر (التصدير) أولاً ثم الطابور.
  • القرار الثلاثي: match⇒تجاهل · likely⇒review_duplicate · new⇒إضافة.
  • الترحيل: بصمات الطابور القديم تُحتسب طازجة فلا يُعاد إرسال مُرسَل سابقاً.
  • الاختبار القاطع: «سوفاج EDP 100مل» و«سوفاج او دو بارفيوم 100مل» ⇒ بصمة
    واحدة ويُكتشف تكراراً.
"""
import pandas as pd
import pytest

import utils.missing_queue_manager as mqm


@pytest.fixture(autouse=True)
def _isolate_queue(tmp_path, monkeypatch):
    """يعزل ملفات الطابور في مجلّد مؤقّت لكل اختبار."""
    monkeypatch.setattr(mqm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(mqm, "BRAND_CATALOG_FILE", tmp_path / "brand_catalog.csv")
    monkeypatch.setattr(mqm, "CATEGORY_CATALOG_FILE", tmp_path / "category_catalog.csv")
    monkeypatch.setattr(mqm, "BRANDS_QUEUE_FILE", tmp_path / "missing_brands_queue.csv")
    monkeypatch.setattr(mqm, "PRODUCTS_QUEUE_FILE", tmp_path / "missing_products_queue.csv")
    yield


def _catalog(*rows) -> pd.DataFrame:
    """كتالوج متجر بصيغة تصدير سلة (أعمدة عربية)."""
    return pd.DataFrame(
        [{"أسم المنتج": n, "الماركة": b} for n, b in rows]
    )


# ════════════════════════════════════════════════════════════════════
#  الاختبار القاطع (regression) — انهيار الصيغ لبصمة واحدة
# ════════════════════════════════════════════════════════════════════
def test_decisive_concentration_variants_collapse_to_duplicate() -> None:
    """«سوفاج EDP 100مل» يُكتشف تكراراً لـ«سوفاج او دو بارفيوم 100مل» في الكتالوج."""
    catalog = _catalog(("ديور سوفاج او دو بارفيوم 100مل", "Dior"))
    res = mqm.enqueue_missing_products(
        [{"name": "ديور سوفاج EDP 100مل", "brand": "Dior"}],
        our_catalog_df=catalog,
    )
    assert res["added"] == 0
    assert res["skipped_dup"] == 1
    assert res["review_duplicate"] == 0


# ════════════════════════════════════════════════════════════════════
#  القرار الثلاثي
# ════════════════════════════════════════════════════════════════════
def test_new_product_is_added() -> None:
    """منتج لا يشبه الكتالوج ⇒ يُضاف فعلاً (لا يُحجب جديد حقيقي)."""
    catalog = _catalog(("ديور سوفاج 100مل", "Dior"))
    res = mqm.enqueue_missing_products(
        [{"name": "نيشان هاسيت بلوش 50مل", "brand": "Nishane"}],
        our_catalog_df=catalog,
    )
    assert res["added"] == 1
    assert res["skipped_dup"] == 0
    pq = mqm._read_csv(mqm.PRODUCTS_QUEUE_FILE, mqm.PRODUCTS_QUEUE_COLS)
    assert (pq["product_name"] == "نيشان هاسيت بلوش 50مل").any()
    assert pq.iloc[0]["fingerprint"]  # البصمة مخزّنة


def test_same_product_different_size_is_review_duplicate() -> None:
    """نفس المنتج بحجم مختلف ⇒ likely ⇒ يُحجَز review_duplicate (لا يُرسَل تلقائياً)."""
    catalog = _catalog(("ديور سوفاج 100مل", "Dior"))
    res = mqm.enqueue_missing_products(
        [{"name": "ديور سوفاج 50مل", "brand": "Dior"}],
        our_catalog_df=catalog,
    )
    assert res["review_duplicate"] == 1
    assert res["added"] == 0
    review = mqm.get_review_duplicate_products()
    assert len(review) == 1
    assert review[0]["status"] == "review_duplicate"
    assert review[0]["dup_reason"]                 # سبب الوسم محفوظ
    # محجوز للمراجعة ⇒ ليس ضمن الجاهزة للإرسال
    assert mqm.get_ready_products() == []


def test_exact_duplicate_is_skipped() -> None:
    catalog = _catalog(("لطافة اخلاص 100مل", "Lattafa"))
    res = mqm.enqueue_missing_products(
        [{"name": "لطافة اخلاص 100مل", "brand": "Lattafa"}],
        our_catalog_df=catalog,
    )
    assert res["skipped_dup"] == 1 and res["added"] == 0


# ════════════════════════════════════════════════════════════════════
#  الترحيل / التوافق الخلفي — الطابور القديم (بلا عمود fingerprint)
# ════════════════════════════════════════════════════════════════════
def test_backfill_old_queue_prevents_resend() -> None:
    """صف قديم (مُرسَل، بلا بصمة مخزّنة) يمنع إعادة إرسال نفس المنتج بصيغة أخرى."""
    # طابور قديم بالأعمدة الأصلية (بدون fingerprint/dup_reason)
    legacy_cols = [
        "product_key", "brand_key", "brand_name", "product_name", "price",
        "comp_price", "sku", "image_url", "description", "category_name",
        "category_id", "status", "discovered_at", "sent_at", "error_msg",
    ]
    legacy = pd.DataFrame([{
        **{c: "" for c in legacy_cols},
        "product_key": "OLD1", "brand_name": "Dior",
        "product_name": "ديور سوفاج او دو بارفيوم 100مل", "status": "sent_success",
    }])
    legacy.to_csv(mqm.PRODUCTS_QUEUE_FILE, index=False, encoding="utf-8-sig")

    # نفس المنتج بصيغة مختلفة، بلا كتالوج ⇒ يجب كشفه عبر بصمة الطابور المُحتسبة طازجة
    res = mqm.enqueue_missing_products(
        [{"name": "ديور سوفاج EDP 100مل", "brand": "Dior"}],
        our_catalog_df=None,
    )
    assert res["added"] == 0
    assert res["skipped_dup"] == 1


# ════════════════════════════════════════════════════════════════════
#  الأولوية: كتالوج المتجر ← ثم الطابور
# ════════════════════════════════════════════════════════════════════
def test_catalog_ref_wins_over_queue() -> None:
    """عند تطابق الكتالوج والطابور معاً، المرجع المُعاد من الكتالوج (أساسي)."""
    from engines.duplicate_detector import is_duplicate, MATCH
    queue_df = pd.DataFrame([{"product_name": "ديور سوفاج 100مل",
                              "brand_name": "Dior", "sku": "", "product_key": "Q1"}])
    catalog = _catalog(("ديور سوفاج 100مل", "Dior"))
    idx = mqm._build_dedup_index(catalog, queue_df)
    res = is_duplicate({"name": "ديور سوفاج 100مل", "brand": "Dior"}, idx, use_fuzzy=False)
    assert res.decision == MATCH
    assert str(res.matched).startswith("catalog:")


def test_within_batch_duplicate_skipped() -> None:
    """تكرار داخل الدفعة نفسها يُلتقط (idx.register) فلا يُضاف مرّتين."""
    res = mqm.enqueue_missing_products(
        [
            {"name": "ديور سوفاج EDP 100مل", "brand": "Dior"},
            {"name": "ديور سوفاج او دو بارفيوم 100مل", "brand": "Dior"},
        ],
        our_catalog_df=None,
    )
    assert res["added"] == 1
    assert res["skipped_dup"] == 1


# ════════════════════════════════════════════════════════════════════
#  بوابة الإرسال الحيّ — screen_for_duplicates
# ════════════════════════════════════════════════════════════════════
def test_screen_drops_catalog_duplicate() -> None:
    catalog = _catalog(("ديور سوفاج او دو بارفيوم 100مل", "Dior"))
    screen = mqm.screen_for_duplicates(
        [{"name": "ديور سوفاج EDP 100مل", "brand": "Dior"}],
        catalog, persist_review=False,
    )
    assert screen.summary["match"] == 1
    assert screen.kept == []


def test_screen_keeps_genuinely_new() -> None:
    catalog = _catalog(("ديور سوفاج 100مل", "Dior"))
    screen = mqm.screen_for_duplicates(
        [{"name": "نيشان هاسيت بلوش 50مل", "brand": "Nishane"}],
        catalog, persist_review=False,
    )
    assert screen.summary["new"] == 1
    assert len(screen.kept) == 1


def test_screen_holds_likely_for_review() -> None:
    """نفس المنتج بحجم مختلف ⇒ likely ⇒ يُحجَز (لا يُرسَل، يُحفَظ review_duplicate)."""
    catalog = _catalog(("ديور سوفاج 100مل", "Dior"))
    screen = mqm.screen_for_duplicates(
        [{"name": "ديور سوفاج 50مل", "brand": "Dior"}],
        catalog, persist_review=True,
    )
    assert screen.summary["likely"] == 1
    assert screen.kept == []
    assert len(mqm.get_review_duplicate_products()) == 1


def test_screen_matches_sent_not_pending() -> None:
    """القاعدة الحاسمة: يطابق ضدّ sent_success فقط، لا ضدّ المعلّق (ready/waiting)."""
    cols = mqm.PRODUCTS_QUEUE_COLS
    pd.DataFrame([
        {**{c: "" for c in cols}, "product_key": "S1", "brand_name": "Dior",
         "product_name": "ديور سوفاج 100مل", "status": "sent_success"},
        {**{c: "" for c in cols}, "product_key": "R1", "brand_name": "Lattafa",
         "product_name": "لطافة اخلاص 100مل", "status": "ready_to_send"},
    ]).to_csv(mqm.PRODUCTS_QUEUE_FILE, index=False, encoding="utf-8-sig")

    screen = mqm.screen_for_duplicates(
        [
            {"name": "ديور سوفاج EDP 100مل", "brand": "Dior"},   # مطابق مُرسَل ⇒ يُسقَط
            {"name": "لطافة اخلاص 100مل", "brand": "Lattafa"},    # معلّق فقط ⇒ يبقى جديداً
        ],
        our_catalog_df=None, persist_review=False,
    )
    assert screen.summary["match"] == 1
    assert screen.summary["new"] == 1


def test_screen_empty_input() -> None:
    screen = mqm.screen_for_duplicates([], None)
    assert screen.summary == {"new": 0, "likely": 0, "match": 0}


# ════════════════════════════════════════════════════════════════════
#  مراجعة «محتمل مكرر»: ترقية / إزالة
# ════════════════════════════════════════════════════════════════════
def _seed_review() -> str:
    """يحجز منتجاً واحداً كـ review_duplicate ويعيد مفتاحه."""
    catalog = _catalog(("ديور سوفاج 100مل", "Dior"))
    mqm.enqueue_missing_products(
        [{"name": "ديور سوفاج 50مل", "brand": "Dior"}], our_catalog_df=catalog,
    )
    review = mqm.get_review_duplicate_products()
    assert len(review) == 1
    return review[0]["product_key"]


def test_approve_review_promotes_to_ready() -> None:
    pkey = _seed_review()
    assert mqm.approve_review_duplicate([pkey]) == 1
    assert mqm.get_review_duplicate_products() == []
    assert any(p["product_key"] == pkey for p in mqm.get_ready_products())


def test_dismiss_review_removes_from_queue() -> None:
    pkey = _seed_review()
    assert mqm.dismiss_review_duplicate([pkey]) == 1
    assert mqm.get_review_duplicate_products() == []
    pq = mqm._read_csv(mqm.PRODUCTS_QUEUE_FILE, mqm.PRODUCTS_QUEUE_COLS)
    assert not (pq["product_key"] == pkey).any()


# ════════════════════════════════════════════════════════════════════
#  التدرّج التلقائي لكتالوج المتجر عبر Salla API عند غياب التصدير اليدوي
# ════════════════════════════════════════════════════════════════════
def test_api_catalog_fallback_when_no_export(monkeypatch) -> None:
    import utils.salla_api as sapi
    catalog = _catalog(("ديور سوفاج او دو بارفيوم 100مل", "Dior"))
    monkeypatch.setattr(sapi, "get_store_catalog_df", lambda *a, **k: catalog)
    # بلا تصدير يدوي (our_catalog_df=None) ⇒ يُسحَب كتالوج المتجر من API ويُكتشف التكرار
    res = mqm.enqueue_missing_products(
        [{"name": "ديور سوفاج EDP 100مل", "brand": "Dior"}], our_catalog_df=None,
    )
    assert res["added"] == 0
    assert res["skipped_dup"] == 1
