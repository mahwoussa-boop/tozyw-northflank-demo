from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import ai_engine


def test_stress_probe_aggregates_safe_metrics_without_responses(monkeypatch):
    def fake_call_ai(prompt, page="general", json_mode=False):
        assert page == "review"
        assert "Dior Sauvage" in prompt
        return {"success": True, "source": "OpenRouter", "response": "raw model response"}

    monkeypatch.setattr(ai_engine, "call_ai", fake_call_ai)

    report = ai_engine.run_ai_stress_probe(total_requests=4, max_concurrency=2)

    assert report["requested"] == 4
    assert report["concurrency"] == 2
    assert report["succeeded"] == 4
    assert report["failed"] == 0
    assert report["success_rate_percent"] == 100.0
    assert report["providers"] == {"OpenRouter": 4}
    assert "response" not in report


@pytest.mark.parametrize(
    ("total_requests", "max_concurrency"),
    [(0, 1), (7, 1), (1, 0), (1, 3)],
)
def test_stress_probe_rejects_unsafe_limits(total_requests, max_concurrency):
    with pytest.raises(ValueError):
        ai_engine.run_ai_stress_probe(
            total_requests=total_requests,
            max_concurrency=max_concurrency,
        )
