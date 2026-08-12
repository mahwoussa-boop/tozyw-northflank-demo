"""tests/test_brand_logo.py — جلب شعار الماركة عبر Google CSE (بلا شبكة، محقون).

يغطّي: استخراج الرابط من رد CSE (خالص)، الاستعلام المحقون (200/خطأ/غير مُهيّأ)،
والكاش (ذاكرة + قرص + سلبي) عبر مسار كاش معزول ومُحقِن بديل عن Google.
"""
import utils.brand_logo as bl


# ── pick_logo_url (خالص) ────────────────────────────────────────────────────
def test_pick_logo_url_first_valid_image() -> None:
    js = {"items": [
        {"link": "https://x.com/page"},                 # ليس صورة
        {"link": "https://cdn.example.com/lattafa.png"},  # أول صورة صالحة
        {"link": "https://cdn.example.com/other.jpg"},
    ]}
    assert bl.pick_logo_url(js) == "https://cdn.example.com/lattafa.png"


def test_pick_logo_url_thumbnail_fallback() -> None:
    js = {"items": [{"link": "https://x.com/noext",
                     "image": {"thumbnailLink": "https://t.com/thumb"}}]}
    assert bl.pick_logo_url(js) == "https://t.com/thumb"


def test_pick_logo_url_empty_when_none() -> None:
    assert bl.pick_logo_url({}) == ""
    assert bl.pick_logo_url({"items": []}) == ""


# ── _search_google_logo (محقون) ─────────────────────────────────────────────
class _Resp:
    def __init__(self, status, js=None):
        self.status_code = status
        self._js = js or {}

    def json(self):
        return self._js


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CSE_KEY", "k")
    monkeypatch.setenv("GOOGLE_CSE_ID", "cx")


def test_search_returns_url_on_200(monkeypatch) -> None:
    _configure(monkeypatch)
    js = {"items": [{"link": "https://cdn.x/logo.png"}]}
    out = bl._search_google_logo("Lattafa", _get=lambda u, **k: _Resp(200, js))
    assert out == "https://cdn.x/logo.png"


def test_search_negative_on_non_200(monkeypatch) -> None:
    _configure(monkeypatch)
    out = bl._search_google_logo("X", _get=lambda u, **k: _Resp(429))
    assert out == ""   # هُيّئ ولم ينجح ⇒ كاش سلبي


def test_search_none_when_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_CSE_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    assert bl._search_google_logo("X") is None


# ── get_brand_logo (كاش معزول + حقن) ────────────────────────────────────────
def test_get_brand_logo_caches_in_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRAND_LOGO_CACHE_PATH", str(tmp_path / "logos.csv"))
    bl._MEM.clear()
    calls = {"n": 0}

    def fetch(name):
        calls["n"] += 1
        return "https://cdn.x/lattafa.png"

    assert bl.get_brand_logo("Lattafa", _fetch=fetch) == "https://cdn.x/lattafa.png"
    assert bl.get_brand_logo("lattafa ", _fetch=fetch) == "https://cdn.x/lattafa.png"
    assert calls["n"] == 1   # المطابقة مطبَّعة ⇒ مكالمة واحدة فقط


def test_get_brand_logo_persists_to_disk(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRAND_LOGO_CACHE_PATH", str(tmp_path / "logos.csv"))
    bl._MEM.clear()
    bl.get_brand_logo("Armaf", _fetch=lambda n: "https://cdn.x/armaf.png")
    bl._MEM.clear()  # تفريغ الذاكرة ⇒ القراءة التالية من القرص
    hit = {"n": 0}

    def fetch(name):
        hit["n"] += 1
        return "SHOULD_NOT_BE_CALLED"

    assert bl.get_brand_logo("Armaf", _fetch=fetch) == "https://cdn.x/armaf.png"
    assert hit["n"] == 0   # جاء من كاش القرص


def test_get_brand_logo_negative_cached(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRAND_LOGO_CACHE_PATH", str(tmp_path / "logos.csv"))
    bl._MEM.clear()
    calls = {"n": 0}

    def fetch(name):
        calls["n"] += 1
        return ""   # مُهيّأ لكن لا شعار

    assert bl.get_brand_logo("Unknown", _fetch=fetch) == ""
    assert bl.get_brand_logo("Unknown", _fetch=fetch) == ""
    assert calls["n"] == 1   # السلبي يُخزَّن ⇒ لا يُعاد الاستعلام


def test_get_brand_logo_transient_none_not_cached(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRAND_LOGO_CACHE_PATH", str(tmp_path / "logos.csv"))
    bl._MEM.clear()
    calls = {"n": 0}

    def fetch(name):
        calls["n"] += 1
        return None   # خطأ عابر/غير مُهيّأ

    assert bl.get_brand_logo("Brand", _fetch=fetch) == ""
    assert bl.get_brand_logo("Brand", _fetch=fetch) == ""
    assert calls["n"] == 2   # لا يُخزَّن ⇒ يُعاد المحاولة في المرّة التالية


def test_get_brand_logo_empty_name() -> None:
    assert bl.get_brand_logo("") == ""
    assert bl.get_brand_logo("   ") == ""


# ── تكامل الحمولة: _brand_logo في make_helper ───────────────────────────────
def test_make_helper_brand_logo_prefers_explicit() -> None:
    from utils.make_helper import _brand_logo
    # شعار صريح في الصف ⇒ يُستخدم بلا استعلام Google
    p = {"صورة شعار الماركة": "https://cdn.x/explicit.png"}
    assert _brand_logo(p, "Lattafa") == "https://cdn.x/explicit.png"


def test_make_helper_brand_logo_empty_when_no_brand() -> None:
    from utils.make_helper import _brand_logo
    assert _brand_logo({}, "") == ""
