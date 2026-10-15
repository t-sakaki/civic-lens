"""生成系API（分析・開示請求書・審査請求書・SDGs採点）のIPごとのレート制限"""
import pytest
from fastapi.testclient import TestClient

import cost_guard


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test")
    monkeypatch.setenv("GENERATION_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("GENERATION_RATE_LIMIT_PER_HOUR", "100")
    cost_guard.reset_state()
    from app import app
    yield TestClient(app)
    cost_guard.reset_state()


def _ip(ip):
    return {"X-Forwarded-For": f"{ip}, 10.0.0.1"}


def test_analyze_429_with_japanese_message_and_retry_after(client):
    data = {"user_input": "安城市の補助金の使途が不透明", "target_authority": "anjo-city"}
    assert client.post("/api/analyze", data=data, headers=_ip("1.1.1.1")).status_code == 200
    assert client.post("/api/analyze", data=data, headers=_ip("1.1.1.1")).status_code == 200
    r = client.post("/api/analyze", data=data, headers=_ip("1.1.1.1"))
    assert r.status_code == 429
    assert "待ってから" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0
    # 別のIP（X-Forwarded-For の先頭）は影響を受けない
    assert client.post("/api/analyze", data=data, headers=_ip("2.2.2.2")).status_code == 200


def test_limit_is_shared_across_generation_endpoints(client):
    h = _ip("3.3.3.3")
    assert client.post("/api/sdgs", data={"text": "公園の伐採"}, headers=h).status_code == 200
    assert client.post("/api/disclosure-request", data={"user_input": "x", "target_authority": "nagoya-city"},
                       headers=h).status_code == 200
    r = client.post("/api/review-request", data={"non_disclosure_decision": "不開示", "target_authority": "nagoya-city",
                                                 "alleged_ground": "法人情報"}, headers=h)
    assert r.status_code == 429


def test_hourly_limit(client, monkeypatch):
    monkeypatch.setenv("GENERATION_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("GENERATION_RATE_LIMIT_PER_HOUR", "1")
    h = _ip("4.4.4.4")
    assert client.post("/api/sdgs", data={"text": "a"}, headers=h).status_code == 200
    r = client.post("/api/sdgs", data={"text": "a"}, headers=h)
    assert r.status_code == 429 and "分ほど" in r.json()["detail"]


def test_disabled_when_zero(client, monkeypatch):
    monkeypatch.setenv("GENERATION_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("GENERATION_RATE_LIMIT_PER_HOUR", "0")
    for _ in range(5):
        assert client.post("/api/sdgs", data={"text": "a"}, headers=_ip("5.5.5.5")).status_code == 200


def test_ui_shows_429_message():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert "res.status === 429" in html
