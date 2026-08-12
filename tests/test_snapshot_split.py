# -*- coding: utf-8 -*-
"""tests/test_snapshot_split.py — حرّاس تقسيم اللقطة (صيغة 3).

الخلفية (قياس 2026-07-25): ``persist_results`` كان يكتب اللقطة كاملة (72.8 م.ب)
بكلفة **1.42 ثانية** عند كل ضغطة زر — وهو مستدعىً من ~22 موضعاً في الواجهة.
صار يكتب **ما تغيّر فقط** إلى ملف مستقلّ لكل إطار.

الخطر الذي تحرسه هذه الاختبارات: كشف «ما تغيّر» يعتمد على أن الواجهة تستبدل
الإطار بكائن جديد ولا تعدّله في مكانه. لو خُرق هذا الافتراض يوماً، سقطت كتابةٌ
صامتاً وضاع قرار تسعير — لذلك يوجد هنا حارس مصدر صريح.
"""
import json
import re
from pathlib import Path

import pandas as pd
import pytest

import ui.state_manager as sm
from ui.state_manager import AppState, load_snapshot_raw, snapshot_exists

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_SNAPSHOT_PATH", str(tmp_path / "ui_session.json"))
    return tmp_path


def _state() -> AppState:
    state = AppState()
    state.our_catalog = pd.DataFrame({"المنتج": ["أ", "ب"], "سعر المنتج": [10.0, 20.0]})
    state.sections = {
        "price_raise": pd.DataFrame({"المنتج": ["أ"], "سعر": [10.0]}),
        "review": pd.DataFrame({"المنتج": ["ب"]}),
    }
    state.missing_df = pd.DataFrame({"منتج_المنافس": ["ج"]})
    return state


# ── 1) الكتابة الانتقائية: ما لم يتغيّر لا يُعاد كتابته ───────────────────

def _spy_writes(monkeypatch) -> list[str]:
    """يسجّل أسماء الملفّات المكتوبة فعلياً.

    لا نقيس ``st_mtime`` — دقّة ساعة ويندوز ~15م.ث فكتابتان متتاليتان تحملان
    الطابع نفسه، فيمرّ الاختبار كذباً. التجسّس على الكتابة يقيس النيّة مباشرةً.
    """
    written: list[str] = []
    original = sm._atomic_write

    def _spy(path, text):
        written.append(Path(path).name)
        original(path, text)

    monkeypatch.setattr(sm, "_atomic_write", _spy)
    return written


def test_unchanged_frames_are_not_rewritten(snap, monkeypatch):
    state = _state()
    assert state.persist_results() is True

    written = _spy_writes(monkeypatch)
    # تغيير قسم واحد فقط (استبدال بكائن جديد — كما تفعل الواجهة فعلاً)
    state.sections["review"] = pd.DataFrame({"المنتج": ["ب", "د"]})
    assert state.persist_results() is True

    assert "sections.review.json" in written, "القسم المتغيّر يجب أن يُعاد كتابته"
    for name in ("our_catalog.json", "missing_df.json", "sections.price_raise.json"):
        assert name not in written, f"{name} لم يتغيّر فيجب ألّا يُعاد كتابته"
    assert "_meta.json" in written        # الوصف الصغير يُكتب دائماً
    # والقسم الجديد وصل القرص فعلاً
    fresh = AppState()
    fresh.restore_results()
    assert fresh.sections["review"]["المنتج"].tolist() == ["ب", "د"]


def test_full_forces_rewrite_of_everything(snap, monkeypatch):
    state = _state()
    state.persist_results()
    written = _spy_writes(monkeypatch)
    assert state.persist_results(full=True) is True
    for name in ("our_catalog.json", "missing_df.json",
                 "sections.price_raise.json", "sections.review.json"):
        assert name in written, f"full=True يجب أن يُعيد كتابة {name}"


def test_deleted_file_is_rewritten_even_if_unchanged(snap):
    """لو فُقد ملف إطار (حذف/تلف) تُعاد كتابته رغم أن البصمة لم تتغيّر."""
    state = _state()
    state.persist_results()
    target = snap / "ui_session" / "sections.price_raise.json"
    target.unlink()
    assert state.persist_results() is True
    assert target.exists()


def test_stale_section_file_is_pruned(snap):
    """قسم اختفى بين تحليلين ⇒ يُحذف ملفّه فلا يُستعاد شبحاً."""
    state = _state()
    state.persist_results()
    ghost = snap / "ui_session" / "sections.review.json"
    assert ghost.exists()
    state.sections.pop("review")
    state.persist_results()
    assert not ghost.exists()
    fresh = AppState()
    fresh.restore_results()
    assert "review" not in fresh.sections


# ── 2) سلامة الرحلة: لا فقد بيانات ───────────────────────────────────────

def test_roundtrip_preserves_everything(snap):
    state = _state()
    state.hidden_products = {"softdel_أ"}
    state.processed_price_skus = {"SKU-1"}
    state.processed_missing_urls = {"https://x/1"}
    state.processed_log = [{"key": "k", "name": "أ", "action": "سعر"}]
    assert state.persist_results() is True

    fresh = AppState()
    assert fresh.restore_results() is True
    assert fresh.our_catalog["المنتج"].tolist() == ["أ", "ب"]
    assert sorted(fresh.sections) == ["price_raise", "review"]
    assert len(fresh.sections["price_raise"]) == 1
    assert fresh.missing_df["منتج_المنافس"].tolist() == ["ج"]
    assert fresh.hidden_products == {"softdel_أ"}
    assert fresh.processed_price_skus == {"SKU-1"}
    assert fresh.processed_missing_urls == {"https://x/1"}
    assert fresh.processed_log[0]["name"] == "أ"


def test_restore_stamps_fingerprints_so_first_click_is_cheap(snap, monkeypatch):
    """بعد الاستعادة، ما في الذاكرة = ما على القرص ⇒ أوّل حفظ لا يكتب أي إطار."""
    _state().persist_results()
    fresh = AppState()
    fresh.restore_results()
    written = _spy_writes(monkeypatch)
    assert fresh.persist_results() is True
    assert written == ["_meta.json"], f"أُعيدت كتابة إطارات بلا تغيير: {written}"


def test_lazy_restore_defers_frames_and_preserves_all_on_save(snap):
    """الواجهة تفتح بخفة، لكن أي حفظ لاحق لا يحذف إطاراً لم يُفتح بعد."""
    _state().persist_results()

    fresh = AppState()
    assert fresh.restore_results(eager=False) is True
    assert fresh.sections == {}
    assert fresh.our_catalog is None
    assert fresh.snapshot_frames_fully_loaded is False

    assert fresh.load_snapshot_frames({"sections.review"}) is True
    assert sorted(fresh.sections) == ["review"]
    assert fresh.sections["review"]["المنتج"].tolist() == ["ب"]
    assert fresh.snapshot_frames_fully_loaded is False

    # الحفظ يحمّل الباقي أولاً، فلا يفسر الملفات غير المفتوحة كأقسام محذوفة.
    assert fresh.persist_results() is True
    again = AppState()
    assert again.restore_results() is True
    assert sorted(again.sections) == ["price_raise", "review"]
    assert again.our_catalog["المنتج"].tolist() == ["أ", "ب"]
    assert again.missing_df["منتج_المنافس"].tolist() == ["ج"]


def test_meta_written_last_lists_only_existing_frames(snap):
    state = _state()
    state.persist_results()
    d = snap / "ui_session"
    meta = json.loads((d / "_meta.json").read_text(encoding="utf-8"))
    assert meta["__format__"] == 3
    for _key, fname in meta["frames"].items():
        assert (d / fname).exists(), f"_meta يُعلن {fname} وهو غير موجود"


# ── 3) الترحيل من الصيغة القديمة (لا يفقد المالك تحليله) ─────────────────

def test_legacy_single_file_is_read_and_migrated(snap, monkeypatch):
    legacy = snap / "ui_session.json"
    payload = sm._serialize_snapshot({
        "our_catalog": pd.DataFrame({"المنتج": ["قديم"]}),
        "sections": {"price_raise": pd.DataFrame({"المنتج": ["قديم"]})},
        "missing_df": None, "analysis_results": None,
        "processed_price_skus": {"S1"}, "processed_missing_urls": set(),
        "hidden_products": set(), "processed_log": [],
    })
    legacy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    state = AppState()
    assert state.restore_results() is True
    assert state.sections["price_raise"]["المنتج"].tolist() == ["قديم"]
    assert state.processed_price_skus == {"S1"}
    # رُحِّلت فعلاً: المجلّد صار موجوداً والملف القديم أُحيل جانباً
    assert (snap / "ui_session" / "_meta.json").exists()
    assert not legacy.exists()
    assert (snap / "ui_session.json.pre-v3").exists()
    # ولا تُقرأ الصيغة القديمة ثانيةً
    again = AppState()
    assert again.restore_results() is True
    assert again.sections["price_raise"]["المنتج"].tolist() == ["قديم"]


def test_no_snapshot_returns_false(snap):
    assert AppState().restore_results() is False
    assert snapshot_exists() is False


# ── 4) جسر التوافق لأدوات التقارير ───────────────────────────────────────

def test_load_snapshot_raw_matches_legacy_shape(snap):
    _state().persist_results()
    raw = load_snapshot_raw()
    assert raw["our_catalog"]["__type__"] == "DataFrame"
    assert raw["sections"]["price_raise"]["__type__"] == "DataFrame"
    assert raw["processed_price_skus"]["__type__"] == "set"
    back = sm._deserialize_snapshot(raw)
    assert back["our_catalog"]["المنتج"].tolist() == ["أ", "ب"]
    assert len(back["sections"]["price_raise"]) == 1


# ── 5) الحارس الحرج: لا تعديل في مكانه على إطارات الحالة ─────────────────

_INPLACE = re.compile(
    r"state\.(missing_df|our_catalog|sections)\b[^=\n]*"
    r"(\.at\[|\.iat\[|\.loc\[|\.iloc\[)[^\]]*\]\s*=(?!=)"
)


def test_no_inplace_writes_on_state_frames():
    """كشف «ما تغيّر» يفترض استبدال الإطار لا تعديله في مكانه — هذا حارسه.

    لو أضاف أحدهم يوماً ``state.missing_df.at[i, c] = v`` لصارت البصمة (هوية ·
    طول · أعمدة) ثابتةً رغم تغيّر البيانات ⇒ تُتخطّى الكتابة ويضيع القرار صامتاً.
    عندها: إمّا استبدل الإطار بكائن جديد، أو استدعِ ``persist_results(full=True)``.
    """
    offenders = []
    for folder in ("ui", "services", "engines", "core", "scripts"):
        for path in (_ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in _INPLACE.finditer(text):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(_ROOT)}:{line}")
    assert not offenders, (
        "تعديل في مكانه على إطار حالة يكسر كشف «ما تغيّر» في persist_results: "
        + ", ".join(offenders)
    )
