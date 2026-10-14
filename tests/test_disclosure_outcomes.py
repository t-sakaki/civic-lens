"""disclosure_outcomes: 結果フィードバックと few-shot 事例（自己改善ループ）"""
import pytest

import disclosure_outcomes as do


@pytest.fixture(autouse=True)
def _local(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVIC_LENS_STORAGE", "json")
    monkeypatch.setenv("CIVIC_LENS_OUTCOMES_PATH", str(tmp_path / "outcomes.json"))


def test_record_and_overwrite():
    do.record_outcome("r1", "non_disclosure", "nagoya-city", "名古屋市", "名古屋市", "請求A")
    saved = do.record_outcome("r1", "disclosed", "nagoya-city", "名古屋市", "名古屋市", "請求A")
    assert saved["storage"] == "local" and saved["outcome_label"] == "開示"
    s = do.stats("nagoya-city")
    assert s["total"] == 1 and s["counts"]["disclosed"] == 1 and s["success_rate"] == 1.0


def test_invalid_outcome():
    with pytest.raises(ValueError):
        do.record_outcome("r2", "maybe", "nagoya-city")


def test_similar_successes_prefer_same_authority_and_skip_failures():
    do.record_outcome("a", "disclosed", "anjo-city", "安城市", "安城市", "安城の請求")
    do.record_outcome("b", "partial", "nagoya-city", "名古屋市", "名古屋市", "名古屋の請求")
    do.record_outcome("c", "non_disclosure", "nagoya-city", "名古屋市", "名古屋市", "失敗した請求")
    do.record_outcome("d", "disclosed", "osaka-police", "大阪府警察", "大阪府", "無関係")
    cases = do.find_similar_successes("nagoya-city", "名古屋市")
    assert [c["request_id"] for c in cases] == ["b"]
    text = do.format_few_shot(cases)
    assert "名古屋の請求" in text and "失敗した請求" not in text
    assert do.format_few_shot([]) == ""


def test_api_roundtrip(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test")
    from fastapi.testclient import TestClient
    from app import app
    c = TestClient(app)
    r = c.post("/api/disclosure-outcome", data={"request_id": "x1", "outcome": "partial",
                                                  "target_authority": "nagoya-city", "request_text": "文書X"})
    assert r.status_code == 200 and r.json()["outcome_label"] == "一部開示"
    assert c.post("/api/disclosure-outcome", data={"request_id": "x2", "outcome": "bad"}).status_code == 400
    r = c.post("/api/disclosure-request", data={"user_input": "補助金の使途", "target_authority": "nagoya-city"})
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] and len(body["few_shot_cases"]) == 1