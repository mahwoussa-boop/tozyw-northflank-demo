# -*- coding: utf-8 -*-
"""بصمة الكتالوج: إبطال كاش المُطابِق عند التغيّر الفعلي بدل انتهاء زمني أعمى.

القياس (2026-07-25): بناء مُطابِق الملكية **1,151م.ث دافئاً و5,275 بارداً**،
والبصمة ~19م.ث ⇒ 60×. وكان الكاش على ``ttl=1800`` وحده، فيجمع أسوأ الاتجاهين:
يُسقط الكاش كل 30 دقيقة وإن لم يتغيّر شيء، **ويتأخّر مع ذلك** حتى 30 دقيقة عن
كتالوج تغيّر بعد تحليل. البصمة تُصلح الاتجاهين.
"""
import sqlite3

import pytest

from ui.pages.new_at_competitors import catalog_signature

_DDL = ("CREATE TABLE our_catalog (id INTEGER PRIMARY KEY, product_id TEXT, "
        "product_name TEXT, norm_name TEXT, price REAL, first_seen TEXT, "
        "last_seen TEXT, cost_price REAL)")


@pytest.fixture()
def catalog(monkeypatch, tmp_path):
    """قاعدة كتالوج معزولة + ``run_id`` مُسيطَر عليه (لا لمس للقاعدة الحيّة)."""
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute(_DDL)
    con.commit()
    con.close()
    monkeypatch.setattr("utils.data_paths.get_data_db_path", lambda _n: str(db))
    monkeypatch.setattr("services.analysis_runner.read_progress", lambda: {"run_id": "r1"})

    def _add(name: str, norm: str, last_seen: str = "2026-07-25") -> None:
        c = sqlite3.connect(db)
        c.execute("INSERT INTO our_catalog (product_name, norm_name, last_seen) "
                  "VALUES (?,?,?)", (name, norm, last_seen))
        c.commit()
        c.close()

    def _rename(rowid: int, name: str, norm: str) -> None:
        c = sqlite3.connect(db)
        c.execute("UPDATE our_catalog SET product_name=?, norm_name=? WHERE rowid=?",
                  (name, norm, rowid))
        c.commit()
        c.close()

    return type("Cat", (), {"add": staticmethod(_add), "rename": staticmethod(_rename)})


def test_signature_is_stable_when_nothing_changes(catalog):
    """الأهمّ: بصمة غير مستقرّة = إعادة بناء عند كل رسم = أبطأ من TTL."""
    catalog.add("عطر أ", "عطر ا")
    assert catalog_signature() == catalog_signature() == catalog_signature()


def test_adding_a_product_changes_the_signature(catalog):
    catalog.add("عطر أ", "عطر ا")
    before = catalog_signature()
    catalog.add("عطر ب", "عطر ب")
    assert catalog_signature() != before


def test_renaming_changes_the_signature(catalog):
    """التغيّر الأخطر: العدد ثابت والاسم اختلف — TTL وحده كان يتأخّر عنه."""
    catalog.add("عطر أ", "عطر ا")
    before = catalog_signature()
    catalog.rename(1, "عطر مختلف تماماً", "عطر مختلف تماما")
    assert catalog_signature() != before


def test_new_analysis_changes_the_signature(monkeypatch, catalog):
    """`run_id` هو الإشارة الوحيدة التي تعبر العمليات: المشغّل الخلفي عملية
    منفصلة لا تستطيع إبطال كاش الخادم في الذاكرة."""
    catalog.add("عطر أ", "عطر ا")
    before = catalog_signature()
    monkeypatch.setattr("services.analysis_runner.read_progress", lambda: {"run_id": "r2"})
    assert catalog_signature() != before


def test_missing_database_is_safe_and_stable(monkeypatch, tmp_path):
    """قاعدة غائبة: لا استثناء، وبصمة **ثابتة** — بصمة متغيّرة عند الفشل تحوّل
    عطب قراءة إلى بطء دائم (إعادة بناء عند كل رسم)."""
    monkeypatch.setattr("utils.data_paths.get_data_db_path",
                        lambda _n: str(tmp_path / "nope.db"))
    monkeypatch.setattr("services.analysis_runner.read_progress", lambda: None)
    assert catalog_signature() == catalog_signature()
    assert catalog_signature().startswith("?")


def test_corrupt_progress_file_does_not_break_the_signature(monkeypatch, catalog):
    """`read_progress` يعيد None عند التلف — البصمة تبقى صالحة بلا run_id."""
    catalog.add("عطر أ", "عطر ا")
    monkeypatch.setattr("services.analysis_runner.read_progress", lambda: None)
    sig = catalog_signature()
    assert sig.endswith("|") and not sig.startswith("?")


def test_empty_catalog_is_safe(catalog):
    """كتالوج فارغ: SUM يعيد None — لا انهيار."""
    assert catalog_signature()      # لا استثناء ولا نصّ فارغ


# ── المذكّرة الزمنية: البصمة مرّة كل 60 ثانية لا مرّة كل رسم ──────────────
@pytest.fixture()
def memo(monkeypatch):
    """يعزل المذكّرة العالمية ويعدّ نداءات البصمة الحقيقية."""
    import ui.pages.new_at_competitors as nac

    monkeypatch.setattr(nac, "_sig_memo", (0.0, ""))
    calls = []
    monkeypatch.setattr(nac, "catalog_signature",
                        lambda: (calls.append(1), f"sig{len(calls)}")[1])
    return nac, calls


def test_memo_computes_once_within_the_window(memo):
    """الجوهر: الصفحة تُعاد رسمها عند كل تفاعل؛ بلا مذكّرة تُحسب البصمة (~18م.ث)
    في كل رسم فيتبدّد الكسب. قياس: 50 نداءً مُذكَّراً = 0.02م.ث."""
    nac, calls = memo
    values = {nac._cached_signature() for _ in range(50)}
    assert len(calls) == 1 and values == {"sig1"}


def test_memo_recomputes_after_the_window(monkeypatch, memo):
    """شبكة الطزاجة: بعد النافذة تُعاد القراءة فيُكتشف تغيّر الكتالوج."""
    nac, calls = memo
    first = nac._cached_signature()
    stamp, value = nac._sig_memo
    monkeypatch.setattr(nac, "_sig_memo", (stamp - nac._SIG_TTL_SEC - 1, value))
    assert nac._cached_signature() != first and len(calls) == 2


def test_matcher_keys_its_cache_on_the_signature(monkeypatch):
    """حارس الوصل: بصمة تُحسب ولا تُستخدم مفتاحاً = عطب «يُكتب ولا يُقرأ» — نفس
    النمط الذي أُصلح في سجلّ التدقيق اليوم. يُلتقط سلوكياً لا بفحص المصدر."""
    import ui.pages.new_at_competitors as nac

    seen = []

    class _FakeSt:
        @staticmethod
        def cache_resource(**_kw):
            def deco(_fn):
                def wrapper(sig):
                    seen.append(sig)
                    return "MATCHER"
                return wrapper
            return deco

    monkeypatch.setattr(nac, "_matcher_cache", None)
    monkeypatch.setattr(nac, "_cached_signature", lambda: "SIG-X")
    assert nac._matcher(_FakeSt()) == "MATCHER"
    assert seen == ["SIG-X"], "المُطابِق لا يُفتَّح ببصمة الكتالوج"


def test_memo_does_not_serve_an_empty_signature(monkeypatch):
    """بصمة فارغة ليست نتيجة صالحة: لو خُزّنت وقُدّمت لتجمّد المُطابِق على مفتاح
    فارغ حتى بعد تغيّر الكتالوج. الشرط ``_sig_memo[1] and …`` يمنع ذلك."""
    import ui.pages.new_at_competitors as nac

    monkeypatch.setattr(nac, "_sig_memo", (0.0, ""))
    calls = []
    monkeypatch.setattr(nac, "catalog_signature",
                        lambda: (calls.append(1), "")[1])
    assert [nac._cached_signature() for _ in range(3)] == ["", "", ""]
    assert len(calls) == 3, "بصمة فارغة قُدّمت من المذكّرة بدل إعادة المحاولة"
