import pytest
from fastapi.testclient import TestClient
from app import app
import agent

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_genai_disabled(monkeypatch):
    """テスト実行時は外部Gemini API通信をモック化して高速化・安定化"""
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))


def test_index_page():
    res = client.get("/")
    assert res.status_code == 200
    assert "Civic Lens" in res.text


def test_api_situations():
    res = client.get("/api/situations")
    assert res.status_code == 200
    data = res.json()
    assert "situations" in data
    assert len(data["situations"]) > 0


def test_api_ordinances():
    res = client.get("/api/ordinances")
    assert res.status_code == 200
    data = res.json()
    assert "ordinances" in data
    assert len(data["ordinances"]) > 0


def test_api_visibility_stats():
    res = client.get("/api/visibility/stats")
    assert res.status_code == 200
    data = res.json()
    assert "total_records" in data
    assert "public_count" in data


def test_api_auth_flow():
    import uuid
    uname = f"api_user_{uuid.uuid4().hex[:6]}"
    pw = "pass12345"

    # 新規登録
    reg_res = client.post("/api/auth/register", data={"username": uname, "password": pw})
    assert reg_res.status_code == 200
    reg_data = reg_res.json()
    assert reg_data["success"] is True
    assert "token" in reg_data
    token = reg_data["token"]

    # ログイン状態確認
    me_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["authenticated"] is True
    assert me_data["user"]["username"] == uname

    # マイ請求履歴取得（初期0件）
    my_res = client.get("/api/auth/my-records", headers={"Authorization": f"Bearer {token}"})
    assert my_res.status_code == 200
    assert "records" in my_res.json()


def test_api_authorities_includes_courts():
    res = client.get("/authorities")
    assert res.status_code == 200
    data = res.json()
    assert "authorities" in data
    categories = {a["category"] for a in data["authorities"]}
    assert "自治体" in categories
    assert "警察" in categories
    assert "裁判所" in categories

    keys = {a["key"] for a in data["authorities"]}
    assert "supreme-court" in keys
    assert "tokyo-district-court" in keys
    assert "nagoya-high-court" in keys


def test_api_disclosure_request_court():
    res = client.post(
        "/api/disclosure-request",
        data={
            "user_input": "最高裁の裁判官会議の議事概要および執務要領を開示してほしい",
            "target_authority": "supreme-court",
            "situation_key": "court_admin",
            "strategy_option": "option-fast",
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert "司法行政文書開示申出書" in data["request_text"]
    assert "最高裁判所" in data["authority"]
    assert data["deadline"]["decision_days"] == 30
    assert any("苦情の申出" in s for s in data["next_steps"])


def test_api_review_request_court():
    res = client.post(
        "/api/review-request",
        data={
            "non_disclosure_decision": "裁判所の事務処理に著しい支障を及ぼすおそれがあるとして不開示決定を受けた",
            "target_authority": "tokyo-district-court",
            "alleged_ground": "第4条第4号",
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert "counter_argument" in data
    assert len(data["counter_argument"]["counter_arguments"]) > 0


def test_api_route_court():
    res = client.post("/api/route", data={"target_authority": "supreme-court"})
    assert res.status_code == 200
    data = res.json()
    assert "office" in data
    assert data["office"]["name"] == "最高裁判所"
    assert "永田町駅" in data["office"]["nearest_station"]

