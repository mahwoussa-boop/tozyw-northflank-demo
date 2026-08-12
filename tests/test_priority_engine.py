# -*- coding: utf-8 -*-
"""اختبارات محرك الأولوية (services/priority_engine.py) — وحدة ج4 MVP قسم سعر أعلى فقط.

يثبّت: (1) الدوال الخالصة (أثر مالي/يقين طلب/ثقة مصدر/بوّابة/سطر سبب) بحالاتها
الحدّية، (2) بناء الكاش من مصادر حقيقية (competitor_products_store +
product_signal_events + competitor_stores)، (3) ذرّية التبديل عند فشل الإدراج
(نفس عطل py3.14 — الكاش القديم يبقى سليماً)، (4) ``urgency_sorted`` يرتّب
العناصر عالية اليقين أعلى ويُبقي البقية بترتيبها الحالي دون كسر أو إخفاء.
"""
import sqlite3

from services.priority_engine import (
    CERTAINTY_GATE,
    NEUTRAL_TRUST,
    compute_urgency,
    demand_certainty,
    financial_impact,
    passes_certainty_gate,
    rebuild_product_priority_scores,
    reason_line,
    source_trust,
    urgency_sorted,
)


# ══════════════════════ الدوال الخالصة ══════════════════════

def test_financial_impact_zero_when_not_more_expensive():
    assert financial_impact(100, 120) == 0.0     # سعرنا أقل من المنافس ⇒ لا فجوة خسارة
    assert financial_impact(100, 100) == 0.0     # تعادل ⇒ لا فجوة
    assert financial_impact(0, 50) == 0.0        # سعر فاسد ⇒ 0 لا استثناء
    assert financial_impact("abc", 50) == 0.0    # قيمة غير رقمية ⇒ 0 (fail-open)


def test_financial_impact_scales_with_rating_count():
    low = financial_impact(150, 100, rating_count=0)
    high = financial_impact(150, 100, rating_count=200)
    assert low == 50.0                # وزن حجم أدنى = 1.0 → 50*1.0
    assert high == 100.0              # وزن حجم أقصى = 2.0 → 50*2.0
    assert low < high


def test_demand_certainty_bounds():
    assert demand_certainty(0) == 0.0
    assert demand_certainty(-3) == 0.0           # قيمة سالبة فاسدة ⇒ 0 لا كسر
    assert demand_certainty(5) == 1.0            # العتبة الكاملة
    assert demand_certainty(50) == 1.0           # لا يتجاوز 1 أبداً
    assert 0 < demand_certainty(2) < 1.0


def test_source_trust_neutral_when_missing():
    assert source_trust([]) == NEUTRAL_TRUST     # غياب بيانات ⇒ محايد، لا عقاب


def test_source_trust_discounted_by_outliers():
    full = source_trust([5.0, 5.0], outlier_fraction=0.0)
    discounted = source_trust([5.0, 5.0], outlier_fraction=0.5)
    assert full == 1.0
    assert discounted == 0.5
    assert source_trust([5.0], outlier_fraction=1.0) == 0.0


def test_passes_certainty_gate_threshold():
    assert passes_certainty_gate(1.0, 1.0) is True
    assert passes_certainty_gate(0.0, 1.0) is False
    assert passes_certainty_gate(CERTAINTY_GATE, 1.0) is True   # الحد الأدنى بالضبط يمرّ


def test_compute_urgency_zero_without_gap():
    assert compute_urgency(100, 120, 0, demand=1.0, trust=1.0) == 0.0


def test_reason_line_mentions_gap_and_demand():
    line = reason_line(150, 100, rating_up_events=3)
    assert "50" in line
    assert "3" in line
    assert "+" in line  # جزءان مدموجان


def test_reason_line_empty_parts_when_no_signal():
    assert reason_line(100, 100, 0) == ""        # لا فجوة ولا طلب ⇒ سطر فارغ لا مختلَق


# ══════════════════════ بناء الكاش من مصادر حقيقية ══════════════════════

def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE competitor_products_store ("
        "norm_name TEXT, competitor TEXT, price REAL, availability INTEGER)"
    )
    conn.execute(
        "CREATE TABLE product_signal_events ("
        "competitor TEXT, norm_name TEXT, run_at TEXT, event TEXT, old_val REAL, new_val REAL)"
    )
    conn.execute(
        "CREATE TABLE competitor_stores ("
        "store_name TEXT PRIMARY KEY, rating_avg REAL, rating_count INTEGER, trust_score REAL)"
    )
    conn.executemany(
        "INSERT INTO competitor_products_store VALUES (?, ?, ?, ?)",
        [
            ("prod_hot", "trusted_store", 90.0, 1),      # طلب مثبت + متجر موثوق
            ("prod_cold", "trusted_store", 80.0, 1),     # لا إشارة طلب حديثة
            ("prod_untracked", "unknown_store", 70.0, 1),  # متجر بلا تقييم ثقة مسجَّل
        ],
    )
    conn.executemany(
        "INSERT INTO product_signal_events VALUES (?, ?, datetime('now'), 'rating_up', ?, ?)",
        [("trusted_store", "prod_hot", 1, 2)] * 6,   # 6 أحداث حديثة ⇒ يقين طلب كامل
    )
    conn.execute(
        "INSERT INTO competitor_stores VALUES ('trusted_store', 4.9, 500, 5.0)"
    )
    conn.commit()
    conn.close()


def test_rebuild_reads_real_sources():
    import tempfile, os
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed(db)
        n = rebuild_product_priority_scores(db)
        assert n == 3   # ثلاثة منتجات متوفّرة بسعر > 0

        conn = sqlite3.connect(db)
        rows = {r[0]: r for r in conn.execute(
            "SELECT norm_name, demand_certainty, source_trust, rating_up_events "
            "FROM product_priority_scores"
        ).fetchall()}
        conn.close()

        hot = rows["prod_hot"]
        assert hot[1] == 1.0            # يقين طلب كامل (6 أحداث ≥ 5)
        assert hot[2] == 1.0            # ثقة كاملة (trust_score=5.0 → 5/5=1.0)
        assert hot[3] == 6

        cold = rows["prod_cold"]
        assert cold[1] == 0.0           # لا أحداث rating_up
        assert cold[2] == 1.0           # نفس المتجر الموثوق

        untracked = rows["prod_untracked"]
        assert untracked[2] == NEUTRAL_TRUST   # متجر بلا سجل ثقة ⇒ محايد لا عقاب
    finally:
        os.remove(db)


def test_rebuild_twice_no_duplication():
    import tempfile, os
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed(db)
        assert rebuild_product_priority_scores(db) == 3
        assert rebuild_product_priority_scores(db) == 3   # لا تراكم، لا خطأ «موجود مسبقاً»
        conn = sqlite3.connect(db)
        cnt = conn.execute("SELECT COUNT(*) FROM product_priority_scores").fetchone()[0]
        conn.close()
        assert cnt == 3
    finally:
        os.remove(db)


def test_rebuild_empty_source_returns_zero():
    import tempfile, os
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE competitor_products_store (norm_name TEXT, competitor TEXT, price REAL, availability INTEGER)")
        conn.execute("CREATE TABLE product_signal_events (competitor TEXT, norm_name TEXT, run_at TEXT, event TEXT, old_val REAL, new_val REAL)")
        conn.execute("CREATE TABLE competitor_stores (store_name TEXT PRIMARY KEY, rating_avg REAL, rating_count INTEGER, trust_score REAL)")
        conn.commit()
        conn.close()
        assert rebuild_product_priority_scores(db) == 0
    finally:
        os.remove(db)


def test_rebuild_missing_tables_returns_zero_no_exception():
    import tempfile, os
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # قاعدة بلا أي جدول مصدري إطلاقاً — يجب ألا ينهار، فقط 0.
        assert rebuild_product_priority_scores(db) == 0
    finally:
        os.remove(db)


def test_rebuild_rollback_preserves_old_cache_on_insert_failure(tmp_path, monkeypatch):
    """ذرّية التبديل: فشل الإدراج ⇒ يبقى الكاش القديم سليماً (نفس عطل py3.14).

    راجع services/top_competitor_service.py::rebuild_top_competitor للمرجع —
    نفس البنية (BEGIN صريح + DROP+CREATE+INSERT داخل معاملة واحدة).
    """
    import services.priority_engine as svc

    db = str(tmp_path / "t.db")
    _seed(db)
    assert rebuild_product_priority_scores(db) == 3

    conn0 = sqlite3.connect(db)
    old = conn0.execute(
        "SELECT norm_name FROM product_priority_scores ORDER BY norm_name"
    ).fetchall()
    conn0.close()
    assert len(old) == 3

    _real_connect = sqlite3.connect

    class _FailInsertConn(sqlite3.Connection):
        def executemany(self, sql, *args, **kwargs):
            if sql.lstrip().upper().startswith("INSERT"):
                raise sqlite3.OperationalError("فشل إدراج مُتعمَّد للاختبار")
            return super().executemany(sql, *args, **kwargs)

    monkeypatch.setattr(
        svc.sqlite3, "connect",
        lambda *a, **k: _real_connect(*a, factory=_FailInsertConn, **k),
    )
    assert rebuild_product_priority_scores(db) == 0
    monkeypatch.undo()

    conn1 = sqlite3.connect(db)
    after = conn1.execute(
        "SELECT norm_name FROM product_priority_scores ORDER BY norm_name"
    ).fetchall()
    conn1.close()
    assert after == old


# ══════════════════════ urgency_sorted (وقت العرض) ══════════════════════

def test_urgency_sorted_ranks_gated_items_first(tmp_path, monkeypatch):
    import pandas as pd
    from conf.constants import COL_COMP_DETAILS, COL_COMP_NAME, COL_COMP_PRICE, COL_OUR_PRICE

    db = str(tmp_path / "t.db")
    _seed(db)
    rebuild_product_priority_scores(db)

    monkeypatch.setattr(
        "services.priority_engine._default_db", lambda: db,
    )

    df = pd.DataFrame([
        {  # منتج بارد (بلا إشارة طلب) — فجوة سعرية كبيرة لكن لا يتصدّر
            COL_OUR_PRICE: 200, COL_COMP_PRICE: 80,
            COL_COMP_NAME: "prod_cold", COL_COMP_DETAILS: "[]",
        },
        {  # منتج ساخن (يقين طلب كامل + ثقة كاملة) — فجوة أصغر لكن يتصدّر
            COL_OUR_PRICE: 100, COL_COMP_PRICE: 90,
            COL_COMP_NAME: "prod_hot", COL_COMP_DETAILS: "[]",
        },
    ])
    out = urgency_sorted(df, db_path=db)
    assert out.iloc[0][COL_COMP_NAME] == "prod_hot"     # عالي اليقين يتصدّر رغم فجوة أصغر
    assert out.iloc[0]["_سبب_الأولوية"] != ""
    assert out.iloc[1]["_سبب_الأولوية"] == ""            # البارد لم يتجاوز البوّابة ⇒ سطر فارغ


def test_urgency_sorted_never_drops_rows():
    import pandas as pd
    from conf.constants import COL_COMP_DETAILS, COL_COMP_NAME, COL_COMP_PRICE, COL_OUR_PRICE

    df = pd.DataFrame([
        {COL_OUR_PRICE: 150, COL_COMP_PRICE: 100, COL_COMP_NAME: "x", COL_COMP_DETAILS: "[]"},
        {COL_OUR_PRICE: 150, COL_COMP_PRICE: 120, COL_COMP_NAME: "y", COL_COMP_DETAILS: "[]"},
    ])
    out = urgency_sorted(df, db_path="/nonexistent/path/does/not/exist.db")
    assert len(out) == 2   # قاعدة غائبة ⇒ يتراجع لـpriority_sorted، لا فقدان صفوف


def test_urgency_sorted_empty_df_passthrough():
    import pandas as pd
    assert urgency_sorted(pd.DataFrame()).empty
    assert urgency_sorted(None) is None
