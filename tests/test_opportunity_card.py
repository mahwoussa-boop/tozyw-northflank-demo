"""tests/test_opportunity_card.py — بطاقة الفرصة الموحّدة (missing/new/oos).

تحرس أن مسار ``kind="missing"`` لم يتغيّر (regression)، وتتحقّق من شارات
جديد/نفذت + الملكية الجديدة. النواة نقية (HTML) فلا حاجة لـ Streamlit.
"""
from ui.components.comparison_card import build_missing_card_html


def _missing_row(conf="green"):
    return {
        "منتج_المنافس": "عطر اختبار لطافة",
        "سعر_المنافس": 100,
        "مستوى_الثقة": conf,
        "عدد_المنافسين": 2,
        "المنافسون": "متجر أ,متجر ب",
        "المنافس": "متجر أ",
        "الماركة": "لطافة",
        "رابط_المنافس": "",
    }


# ── حراسة المفقودات (يجب ألا يتغيّر) ───────────────────────────────────────
def test_missing_default_equals_explicit_kind() -> None:
    row = _missing_row()
    assert build_missing_card_html(row) == build_missing_card_html(row, kind="missing")


def test_missing_green_badge_preserved() -> None:
    out = build_missing_card_html(_missing_row("green"))
    assert "🟢 مؤكد مفقود" in out
    assert "mhw-card" in out
    assert "🆕 جديد" not in out and "🔴 نفذت" not in out


def test_missing_review_badge_preserved() -> None:
    out = build_missing_card_html(_missing_row("review"))
    assert "🔵 محتمل (مراجعة)" in out


# ── جديد / نفذت (السلوك الجديد) ────────────────────────────────────────────
def test_new_kind_shows_new_badge() -> None:
    row = _missing_row()
    row["ownership"] = "missing"
    out = build_missing_card_html(row, kind="new", accent="#10B981")
    assert "🆕 جديد" in out
    assert "🆕 ليس لديك" in out          # شارة الملكية: ليس لدينا
    assert "مؤكد مفقود" not in out


def test_oos_kind_shows_oos_badge() -> None:
    row = _missing_row()
    row["ownership"] = "owned"
    out = build_missing_card_html(row, kind="oos", accent="#EF4444")
    assert "🔴 نفذت" in out
    assert "✅ لديك" in out              # نملكه


def test_new_kind_review_ownership() -> None:
    row = _missing_row()
    row["ownership"] = "review"
    out = build_missing_card_html(row, kind="new")
    assert "🔵 محتمل لديك" in out


def test_multi_store_count_rendered() -> None:
    row = _missing_row()
    row["ownership"] = "missing"
    out = build_missing_card_html(row, kind="new")
    assert "متجر" in out  # عدد المتاجر يظهر في البطاقة
