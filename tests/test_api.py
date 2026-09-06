import pytest
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


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
