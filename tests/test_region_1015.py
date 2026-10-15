"""2026-10-15 地域バグ: 未収録自治体（千代田区など）の推定・経路表示"""
import json

import pytest

import authority_inference as ai
import gemini_models as gm
from ordinance_data import get_ordinance, get_office_info, make_municipality_key, normalize_municipality_name, addressee_name

TEXT = "千代田区の公園の改修工事で、樹木が大量に伐採された。説明会もなく納得できない。"


class _Resp:
    def __init__(self, t):
        self.text = t


class _Client:
    def __init__(self, payload):
        self.models = self
        self.payload = payload

    def generate_content(self, model, contents, config=None):
        return _Resp(json.dumps(self.payload, ensure_ascii=False))


def setup_function(_):
    gm.reset_state()


def test_normalize_municipality():
    assert normalize_municipality_name("千代田区") == "千代田区"
    assert normalize_municipality_name("東京都千代田区") == "東京都千代田区"
    assert normalize_municipality_name("地区") == ""
    assert normalize_municipality_name("<script>区") == ""
    assert normalize_municipality_name("") == ""


def test_generic_authority_for_unregistered_municipality():
    info = get_ordinance(make_municipality_key("東京都千代田区"))
    assert info.authority == "千代田区" and addressee_name(info) == "千代田区長"
    assert info.office.name == "千代田区役所"
    assert get_ordinance("muni:地区") is None


def test_inference_uses_gemini_municipality_outside_registry():
    c = _Client({"authority_key": "", "municipality": "東京都千代田区", "department": "公園課"})
    r = ai.infer_authority(TEXT, client=c)
    assert r["inferred"] and r["authority_key"] == "muni:東京都千代田区"
    assert r["authority"] == "千代田区長" and "AIの推定" in r["note"] and r.get("generic")


def test_inference_falls_back_to_place_in_text():
    c = _Client({"authority_key": "", "municipality": ""})
    r = ai.infer_authority(TEXT, client=c)
    assert r["authority_key"] == "muni:千代田区"
    r2 = ai.infer_authority(TEXT, client=None)
    assert r2["authority_key"] == "muni:千代田区"


def test_no_place_still_asks_user():
    r = ai.infer_authority("駅前の公園の樹木が伐採された", client=None)
    assert r["authority_key"] == "" and r["needs_region"]


def test_office_info_follows_authority_and_never_falls_back_to_anjo():
    assert get_office_info("anjo-city")["name"].startswith("安城市")
    assert get_office_info("muni:千代田区")["name"] == "千代田区役所"
    assert get_office_info("unknown-key") is None
    assert get_office_info("") is None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test")
    from fastapi.testclient import TestClient
    from app import app
    return TestClient(app)


def test_route_api(client):
    r = client.post("/api/route", data={"target_authority": "muni:千代田区"}).json()
    assert r["office"]["name"] == "千代田区役所" and r["route"] is None
    assert "安城" not in json.dumps(r, ensure_ascii=False)
    r = client.post("/api/route", data={"target_authority": "no-such"}).json()
    assert r["office"] is None and r["unknown"] is True
    r = client.post("/api/route", data={"target_authority": "anjo-city"}).json()
    assert r["office"]["name"].startswith("安城市")


def test_disclosure_request_with_generic_municipality(client):
    r = client.post("/api/disclosure-request", data={"user_input": TEXT, "target_authority": "muni:千代田区"})
    assert r.status_code == 200
    assert r.json()["addressee"] == "千代田区長"


def test_analyze_infers_chiyoda_without_gemini(client):
    r = client.post("/api/analyze", data={"user_input": TEXT}).json()
    assert r["anger_analysis"]["target_authority_key"] == "muni:千代田区"
    assert r["authority_inference"]["inferred"] is True


def test_frontend_hooks():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert "if (!data || !data.office)" in html          # 経路: 不明なら非表示
    assert "addDynamicAuthorityOption(inf.authority_key" in html
    assert "lastDisclosureGroupId" in html               # プランボタン再有効化
    assert "const key = 'muni:' + muniReply" in html    # 自治体名の返答は続きとして扱う
