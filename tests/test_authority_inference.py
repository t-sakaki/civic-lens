"""authority_inference: 対象機関が未選択のときの推定（愛知県議会が既定で入るバグの回帰テスト）"""
import json

import gemini_models as gm
import authority_inference as ai


class _Resp:
    def __init__(self, text):
        self.text = text


class _Client:
    def __init__(self, payload):
        self.models = self
        self.payload = payload
        self.calls = 0

    def generate_content(self, model, contents, config=None):
        self.calls += 1
        return _Resp(json.dumps(self.payload, ensure_ascii=False))


def setup_function(_):
    gm.reset_state()


PARK = "駅前の公園の樹木が大量に伐採された。業者は随意契約で説明会もない。"


def test_no_region_and_no_gemini_leaves_authority_empty():
    r = ai.infer_authority(PARK, client=None)
    assert r["inferred"] is False and r["authority_key"] == ""
    assert r["needs_region"] is True and "市区町村" in r["message"]


def test_gemini_unknown_municipality_does_not_fill_assembly():
    c = _Client({"authority_key": "", "municipality": "", "department": "公園緑地課", "reason": "市区町村が不明"})
    r = ai.infer_authority(PARK, client=c)
    assert c.calls == 1
    assert r["authority_key"] == "" and r["needs_region"] is True


def test_gemini_invalid_key_is_rejected():
    c = _Client({"authority_key": "made-up-key", "municipality": "架空市"})
    r = ai.infer_authority(PARK, client=c)
    # 不正なキーは使わない。未収録の自治体名からは汎用の機関（AI推定）を組み立てる
    assert r["authority_key"] == "muni:架空市" and r["inferred"] and "AIの推定" in r["note"]
    c2 = _Client({"authority_key": "made-up-key", "municipality": "<b>x</b>"})
    r2 = ai.infer_authority(PARK, client=c2)
    assert r2["authority_key"] == "" and r2["message"]


def test_gemini_valid_key_is_marked_as_inference():
    c = _Client({"authority_key": "toyota-city", "municipality": "豊田市", "department": "公園緑地課", "reason": "市の公園"})
    r = ai.infer_authority("市内の公園の樹木伐採に納得できない", client=c)
    assert r["inferred"] is True and r["authority_key"] == "toyota-city"
    assert r["note"] == "AIの推定です。提出前に確認してください。"
    assert r["department"] == "公園緑地課"


def test_explicit_place_in_text_wins_without_gemini():
    c = _Client({"authority_key": "aichi-assembly"})
    r = ai.infer_authority("名古屋市の公園で樹木が伐採された", client=c)
    assert r["authority_key"] == "nagoya-city" and r["source"] == "text" and c.calls == 0


def test_api_without_authority_never_defaults_to_assembly(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test")
    from fastapi.testclient import TestClient
    import app as app_module
    monkeypatch.setattr(app_module.authority_inference, "infer_authority",
                        lambda text, client=None: ai._result())
    c = TestClient(app_module.app)
    r = c.post("/api/analyze", data={"user_input": PARK})
    assert r.status_code == 200
    body = r.json()
    assert body["anger_analysis"]["target_authority_key"] == ""
    assert "愛知県議会" not in body["anger_analysis"]["target_authority"]
    assert body["authority_inference"]["needs_region"] is True
    assert body["anger_analysis"]["specific_documents_requested"]  # 文書の特定は従来どおり


def test_api_with_explicit_authority_is_unchanged(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test")
    from fastapi.testclient import TestClient
    import app as app_module
    called = []
    monkeypatch.setattr(app_module.authority_inference, "infer_authority",
                        lambda *a, **k: called.append(1) or ai._result())
    c = TestClient(app_module.app)
    r = c.post("/api/analyze", data={"user_input": PARK, "target_authority": "anjo-city"})
    assert r.status_code == 200 and not called
    assert r.json()["authority_inference"] is None
