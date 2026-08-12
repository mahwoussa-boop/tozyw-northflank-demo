"""tests/test_scrape_opportunity_hook.py — حارس خطاف إعادة بناء كاش الفرص بعد الكشط.

يثبّت أن الخطاف إضافي بحت: يُعاد البناء بعد كشط ناجح، ويُتخطّى بلا حفظ، وفشله
لا يُفشل مسار الاكتمال (القاعدة 6).
"""
import services.opportunity_service as opp
from services.scraper_service import Competitor, ScraperService


def _comp():
    return Competitor(name="t", store_url="", mahally_store_id=1, source="mahally")


def test_hook_survives_rebuild_failure(monkeypatch):
    """rebuild يرمي ⇒ scrape_and_save ينجح ويعيد العدد (لا استثناء يخرج)."""
    svc = ScraperService()
    monkeypatch.setattr(svc, "_scrape_mahally", lambda comp: 7)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(opp, "rebuild_opportunity_scores", _boom)
    assert svc.scrape_and_save(_comp()) == 7


def test_hook_rebuilds_on_success(monkeypatch):
    """كشط ناجح (count>0) ⇒ يُستدعى rebuild مرة واحدة."""
    svc = ScraperService()
    monkeypatch.setattr(svc, "_scrape_mahally", lambda comp: 3)
    calls = []
    monkeypatch.setattr(opp, "rebuild_opportunity_scores",
                        lambda *a, **k: (calls.append(1), 5)[1])
    assert svc.scrape_and_save(_comp()) == 3
    assert calls == [1]


def test_hook_skipped_without_save(monkeypatch):
    """count==0 (لا منتجات) ⇒ لا إعادة بناء."""
    svc = ScraperService()
    monkeypatch.setattr(svc, "_scrape_mahally", lambda comp: 0)
    calls = []
    monkeypatch.setattr(opp, "rebuild_opportunity_scores",
                        lambda *a, **k: (calls.append(1), 0)[1])
    assert svc.scrape_and_save(_comp()) == 0
    assert calls == []


def test_hook_suppressed_with_rebuild_flag(monkeypatch):
    """rebuild=False (تمرّره الجولة) ⇒ كشط ناجح بلا إعادة بناء."""
    svc = ScraperService()
    monkeypatch.setattr(svc, "_scrape_mahally", lambda comp: 4)
    calls = []
    monkeypatch.setattr(opp, "rebuild_opportunity_scores",
                        lambda *a, **k: (calls.append(1), 5)[1])
    assert svc.scrape_and_save(_comp(), rebuild=False) == 4
    assert calls == []


def test_scrape_all_rebuilds_once_at_round_end(tmp_path, monkeypatch):
    """جولة من 3 متاجر ناجحة ⇒ إعادة بناء واحدة (نهاية الجولة) لا 3 (~80s للواحدة)."""
    from services import scraper_service, volatility_service

    monkeypatch.setattr(scraper_service, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(volatility_service, "heat_order", lambda *a, **k: {})
    monkeypatch.setattr(volatility_service, "rebuild_competitor_heat", lambda *a, **k: 0)
    svc = ScraperService()
    comps = [Competitor(name=f"m{i}", store_url="", mahally_store_id=i,
                        source="mahally") for i in (1, 2, 3)]
    monkeypatch.setattr(svc, "list_competitors", lambda: comps)
    monkeypatch.setattr(svc, "_scrape_mahally", lambda comp: 2)
    calls = []
    monkeypatch.setattr(opp, "rebuild_opportunity_scores",
                        lambda *a, **k: (calls.append(1), 5)[1])
    monkeypatch.setattr(svc, "rebuild_new_products_cache", lambda: 0)
    results = svc.scrape_all()
    assert results == {"m1": 2, "m2": 2, "m3": 2}
    assert calls == [1]  # مرة واحدة فقط — لا لكل منافس


def test_scrape_all_no_rebuild_when_nothing_saved(tmp_path, monkeypatch):
    """جولة بلا أي حفظ (كل المتاجر 0/فشل) ⇒ لا إعادة بناء إطلاقاً."""
    from services import scraper_service, volatility_service

    monkeypatch.setattr(scraper_service, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(volatility_service, "heat_order", lambda *a, **k: {})
    monkeypatch.setattr(volatility_service, "rebuild_competitor_heat", lambda *a, **k: 0)
    svc = ScraperService()
    comps = [Competitor(name="m1", store_url="", mahally_store_id=1, source="mahally")]
    monkeypatch.setattr(svc, "list_competitors", lambda: comps)
    monkeypatch.setattr(svc, "_scrape_mahally", lambda comp: 0)
    calls = []
    monkeypatch.setattr(opp, "rebuild_opportunity_scores",
                        lambda *a, **k: (calls.append(1), 0)[1])
    assert svc.scrape_all() == {"m1": 0}
    assert calls == []
