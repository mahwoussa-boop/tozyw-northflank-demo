"""tests/test_closed_loop_ledger.py — اختبارات السجل الكمومي القائم على الهوية
(الدائرة المقفلة v2.0 — الخطوة 3).

يثبت:
- توازن المسار السعيد (مجموعة المُسنَدة == مجموعة المُدخلة).
- 💥 الانهيار إذا نسينا منتجاً (يتيم) — مع ذكر الـ routing_id بالضبط.
- 🚫 منع تسجيل نفس المنتج مرتين.
- 🚫 منع تخصيص منتج لحالتين (تخصيص مزدوج).
- 🚫 منع تخصيص هوية غير مُسجَّلة (شبح).
- الجسر من حالات dec() (STATUS_*) إلى حالات السجل الأربع.
"""
import pandas as pd
import pytest

from engines.closed_loop_engine import (
    STATUS_EXCLUDED,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_REVIEW,
    CompetitorLedger,
    CompetitorUniverse,
    LedgerState,
    build_ledger_assignments,
    make_routing_id,
    reconcile_and_assert,
    to_ledger_state,
)


# ── المسار السعيد: توازن كامل ────────────────────────────────────────────
def test_happy_path_is_balanced() -> None:
    led = CompetitorLedger()
    assert led.register_inputs({"A": [0, 1, 2], "B": [0, 1]}) == 5
    led.assign(("A", 0), LedgerState.CONFIRMED_MATCH)
    led.assign(("A", 1), STATUS_MATCHED)             # عبر الجسر
    led.assign(("A", 2), STATUS_REVIEW)
    led.assign(("B", 0), STATUS_MISSING)
    led.assign(("B", 1), STATUS_EXCLUDED)

    rep = led.assert_closed_loop(context="happy")
    assert rep.balanced
    assert rep.total_input == 5 == rep.assigned
    assert rep.confirmed_match == 2
    assert rep.ai_review_pending == 1
    assert rep.confirmed_missing == 1
    assert rep.excluded_noise == 1


# ── 💥 ينهار إذا نسينا منتجاً (يتيم) ويذكر هويّته ────────────────────────
def test_orphan_product_crashes_naming_routing_id() -> None:
    led = CompetitorLedger()
    led.register_inputs({"A": [0, 1], "B": [0, 1]})  # 4 منتجات
    led.assign(("A", 0), STATUS_MATCHED)
    led.assign(("A", 1), STATUS_MISSING)
    led.assign(("B", 0), STATUS_REVIEW)
    # ('B', 1) نُسي عمداً — يجب أن ينهار النظام.

    with pytest.raises(RuntimeError) as ei:
        led.assert_closed_loop()
    msg = str(ei.value)
    assert "('B', 1)" in msg          # ذكر الـ routing_id المفقود بالضبط
    assert "يتامى" in msg


# ── 🚫 منع تسجيل نفس المنتج مرتين ────────────────────────────────────────
def test_double_registration_forbidden() -> None:
    led = CompetitorLedger()
    led.register(("A", 0))
    with pytest.raises(RuntimeError) as ei:
        led.register(("A", 0))
    assert "مكرّر" in str(ei.value)


# ── 🚫 منع تخصيص منتج لحالتين (تخصيص مزدوج) ──────────────────────────────
def test_double_assignment_forbidden() -> None:
    led = CompetitorLedger()
    led.register(("A", 0))
    led.assign(("A", 0), STATUS_MATCHED)
    with pytest.raises(RuntimeError) as ei:
        led.assign(("A", 0), STATUS_MISSING)
    text = str(ei.value)
    assert "مزدوج" in text
    assert "CONFIRMED_MATCH" in text and "CONFIRMED_MISSING" in text


# ── 🚫 منع تخصيص هوية غير مُسجَّلة (شبح) ──────────────────────────────────
def test_assign_unregistered_forbidden() -> None:
    led = CompetitorLedger()
    led.register(("A", 0))
    with pytest.raises(RuntimeError) as ei:
        led.assign(("X", 9), STATUS_MATCHED)
    assert "غير مُسجَّلة" in str(ei.value)


# ── البرهان على تفوّق الهوية على العدّ: «إسقاط+تكرار» مستحيل وقت الكتابة ──
def test_balanced_drop_plus_dup_is_impossible_at_write_time() -> None:
    # سيناريو الخداع: نُسقط ('A',1) ونكرّر ('A',0) ⇒ العدّ قد يتوازن (2==2)
    # لكن الهوية لا. السجل يمنعه فوراً عند محاولة الإسناد الثاني.
    led = CompetitorLedger()
    led.register_inputs({"A": [0, 1]})
    led.assign(("A", 0), STATUS_MATCHED)
    with pytest.raises(RuntimeError):
        led.assign(("A", 0), STATUS_MISSING)   # تكرار بدل تخصيص ('A',1)


# ── reconcile_and_assert: المسار السعيد ──────────────────────────────────
def test_reconcile_and_assert_happy() -> None:
    src = {"A": [0, 1], "B": [0]}
    assignments = {
        ("A", 0): STATUS_MATCHED,
        ("A", 1): STATUS_REVIEW,
        ("B", 0): STATUS_EXCLUDED,
    }
    rep = reconcile_and_assert(src, assignments, context="recon")
    assert rep.balanced and rep.total_input == 3 == rep.assigned


# ── reconcile_and_assert: يكشف اليتيم ────────────────────────────────────
def test_reconcile_and_assert_detects_orphan() -> None:
    src = {"A": [0, 1, 2]}
    assignments = {("A", 0): STATUS_MATCHED, ("A", 1): STATUS_MISSING}  # ('A',2) ناقص
    with pytest.raises(RuntimeError) as ei:
        reconcile_and_assert(src, assignments)
    assert "('A', 2)" in str(ei.value)


# ── أشكال register_inputs + الجسر ────────────────────────────────────────
def test_register_inputs_list_form_counts() -> None:
    led = CompetitorLedger()
    entries = [
        {"competitor_name": "StoreX", "df": [0, 1, 2]},
        {"competitor_name": "StoreY", "df": [0]},
    ]
    assert led.register_inputs(entries) == 4
    assert led.n_input == 4 and make_routing_id("StoreX", 2) in led._inputs


def test_status_bridge_maps_all_four() -> None:
    assert to_ledger_state(STATUS_MATCHED) is LedgerState.CONFIRMED_MATCH
    assert to_ledger_state(STATUS_MISSING) is LedgerState.CONFIRMED_MISSING
    assert to_ledger_state(STATUS_REVIEW) is LedgerState.AI_REVIEW_PENDING
    assert to_ledger_state(STATUS_EXCLUDED) is LedgerState.EXCLUDED_NOISE
    assert to_ledger_state("CONFIRMED_MATCH") is LedgerState.CONFIRMED_MATCH


# ══════════════════════════════════════════════════════════════════════════
#  محوّل الهوية — build_ledger_assignments
# ══════════════════════════════════════════════════════════════════════════
def _universe() -> CompetitorUniverse:
    return CompetitorUniverse(
        all_ids=frozenset({"1", "2", "3", "4", "5"}),
        name_to_ids={"prodX": ["3"], "prodY": ["4"]},
        noise_ids=frozenset({"5"}),
    )


def test_adapter_priority_and_residual_sweep() -> None:
    uni = _universe()
    sections = {
        "price_raise": pd.DataFrame({"جميع_المنافسين": [[{"product_id": "1"}]]}),
        "review": pd.DataFrame(
            {"جميع_المنافسين": [[{"product_id": "1"}, {"product_id": "2"}, {"product_id": "3"}]]}
        ),
    }
    missing = pd.DataFrame(
        {"منتج_المنافس": ["prodX", "prodY"], "مستوى_الثقة": ["green", "green"]}
    )
    a = build_ledger_assignments(sections, missing, uni)

    assert a["1"] is LedgerState.CONFIRMED_MATCH        # متطابق يتغلّب على مراجعة
    assert a["2"] is LedgerState.AI_REVIEW_PENDING
    assert a["3"] is LedgerState.AI_REVIEW_PENDING      # مراجعة القسم تتغلّب على المفقودات
    assert a["4"] is LedgerState.CONFIRMED_MISSING      # prodY → معرّف 4 (مفقود مؤكد)
    assert a["5"] is LedgerState.EXCLUDED_NOISE         # بقية + ضجيج
    assert len(a) == 5                                   # تغطية كاملة للكون


def test_adapter_no_sweep_leaves_residual() -> None:
    uni = _universe()
    sections = {"price_raise": pd.DataFrame({"جميع_المنافسين": [[{"product_id": "1"}]]})}
    a = build_ledger_assignments(sections, None, uni, sweep_residual=False)
    assert set(a) == {"1"}                               # فقط المتطابق؛ البقية بلا حالة (فجوة مكشوفة)
