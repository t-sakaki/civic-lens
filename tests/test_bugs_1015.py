"""2026-10-15 バグ報告（XSS・OGP・strategy_option）の回帰テスト"""
import re
from pathlib import Path

import pytest

HTML = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
PROD = "https://civic-lens-259303826380.asia-northeast1.run.app"


@pytest.mark.parametrize("raw", [
    "${analysis.pain_summary}",           # (1)
    "${data.request_text}",               # (2)
    "📋 ${data.ordinance_name}", "${data.authority} - ${data.contact}",
    "`<li>${s}</li>`",
    "${r.summary}", "${r.result_excerpt}",  # (4)
    "${r.author_label || '匿名市民'}",
    "${t.name}", "${t.description}", "${t.acceptance_criteria}",  # (5)
])
def test_no_unescaped_interpolation(raw):
    assert raw not in HTML


def test_err_message_escaped_in_innerhtml():  # (3)
    bad = [l for l in HTML.split("\n") if "innerHTML" in l and "${err.message}" in l]
    assert bad == []


def test_ogp_points_to_cloud_run():  # (6)
    assert f'<meta property="og:url" content="{PROD}/">' in HTML
    assert f'property="og:image" content="{PROD}/static/og-image.png"' in HTML
    assert f'name="twitter:image" content="{PROD}/static/og-image.png"' in HTML
    assert "civic-lens-jp.vercel.app/static/og-image" not in HTML


def test_escape_html_defined():
    assert re.search(r"function escapeHtml\(str\)", HTML)


def test_invalid_strategy_option_is_400(monkeypatch):  # (7)
    monkeypatch.setenv("AUTH_SECRET_KEY", "test")
    from fastapi.testclient import TestClient
    from app import app
    c = TestClient(app)
    r = c.post("/api/disclosure-request", data={"user_input": "補助金", "target_authority": "nagoya-city",
                                                 "strategy_option": "bogus"})
    assert r.status_code == 400
    ok = c.post("/api/disclosure-request", data={"user_input": "補助金", "target_authority": "nagoya-city",
                                                  "strategy_option": "option-thorough"})
    assert ok.status_code == 200


def test_agent_rejects_unknown_strategy():
    from agent import get_agent
    from ordinance_data import get_ordinance
    with pytest.raises(ValueError):
        get_agent().generate_disclosure_request("x", get_ordinance("nagoya-city"), strategy_option="bogus")
