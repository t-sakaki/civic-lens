"""開示請求に連なる市民行動（審査請求書・公安委員会苦情・議会一般質問）のテスト"""
import pytest
from fastapi.testclient import TestClient

import agent
import civic_actions
from app import app
from ordinance_data import AUTHORITIES

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_genai_disabled(monkeypatch):
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))


@pytest.mark.parametrize("key,expected", [
    ("anjo-city", "安城市長"),
    ("aichi-police", "愛知県公安委員会"),
    ("metropolitan-police", "東京都公安委員会"),
])
def test_review_addressee(key, expected):
    assert civic_actions.review_addressee(AUTHORITIES[key]) == expected


@pytest.mark.parametrize("key,expected", [
    ("anjo-city", "安城市議会"),
    ("aichi-pref", "愛知県議会"),
    ("aichi-assembly", "愛知県議会"),
    ("osaka-police", "大阪府議会"),
    ("metropolitan-police", "東京都議会"),
    ("supreme-court", None),
])
def test_council_name(key, expected):
    assert civic_actions.council_name(AUTHORITIES[key]) == expected


def test_review_request_returns_formal_document_and_deadline():
    res = client.post("/api/review-request", data={
        "non_disclosure_decision": "海外視察の精算内訳書を請求したが一部開示決定を受けた",
        "target_authority": "anjo-city",
        "alleged_ground": "第7条第2号",
        "decision_type": "partial_disclosure",
        "decision_date": "2026-09-01",
    })
    assert res.status_code == 200
    data = res.json()
    text = data["review_request_text"]
    assert text.startswith("# 審査請求書")
    assert "安城市長 御中" in text
    assert "一部開示決定" in text
    assert "審査請求の趣旨" in text and "教示" in text
    assert data["review_addressee"] == "安城市長"
    assert data["review_deadline"] == "2026-11-30"


def test_review_request_police_goes_to_public_safety_commission():
    res = client.post("/api/review-request", data={
        "non_disclosure_decision": "捜査費の支出文書が不開示とされた",
        "target_authority": "aichi-police",
        "alleged_ground": "第5条第3号",
    })
    assert res.status_code == 200
    assert "愛知県公安委員会 御中" in res.json()["review_request_text"]


def test_review_request_court_is_complaint_form():
    res = client.post("/api/review-request", data={
        "non_disclosure_decision": "事務処理要領が不開示とされた",
        "target_authority": "tokyo-district-court",
        "alleged_ground": "第4条第4号",
    })
    assert res.status_code == 200
    assert "苦情申出書" in res.json()["review_request_text"]


def test_review_request_rejects_bad_date():
    res = client.post("/api/review-request", data={
        "non_disclosure_decision": "x", "target_authority": "anjo-city",
        "alleged_ground": "第7条", "decision_date": "9月1日",
    })
    assert res.status_code == 400


def test_police_complaint_direct_party():
    res = client.post("/api/police-complaint", data={
        "user_input": "職務質問で長時間にわたり所持品検査を強要された",
        "target_authority": "kanagawa-police",
        "incident_datetime": "2026年9月10日 21時頃",
        "incident_place": "横浜駅西口",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["commission"] == "神奈川県公安委員会"
    assert "警察法第79条第1項" in data["complaint_text"]
    assert "横浜駅西口" in data["complaint_text"]


def test_police_complaint_non_party_becomes_opinion():
    res = client.post("/api/police-complaint", data={
        "user_input": "報道された不祥事について",
        "target_authority": "aichi-police",
        "is_direct_party": "false",
    })
    assert res.status_code == 200
    assert res.json()["complaint_text"].startswith("# 意見・要望書")


def test_police_complaint_rejects_non_police():
    res = client.post("/api/police-complaint", data={
        "user_input": "x", "target_authority": "anjo-city",
    })
    assert res.status_code == 400


def test_council_question_three_questions_and_script():
    res = client.post("/api/council-question", data={
        "user_input": "市長の海外視察の費用が不透明",
        "target_authority": "anjo-city",
        "requested_documents": "- 復命書\n- 精算内訳書",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["council"] == "安城市議会"
    assert len(data["questions"]) == 3
    assert all(q["points"] for q in data["questions"])
    assert "復命書" in data["questions"][1]["points"][1]
    assert data["script"].startswith("議長")


def test_council_question_rejects_court():
    res = client.post("/api/council-question", data={
        "user_input": "x", "target_authority": "supreme-court",
    })
    assert res.status_code == 400
