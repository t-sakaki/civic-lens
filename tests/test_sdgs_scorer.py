"""sdgs_scorer: 17目標×169ターゲットの怒りスコアリング"""
import json

import gemini_models as gm
import sdgs_scorer as ss


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def __init__(self, payload):
        self.models = self
        self._payload = payload

    def generate_content(self, model, contents, config=None):
        return _Resp(self._payload)


def setup_function(_):
    gm.reset_state()


def test_official_target_count_is_169():
    assert ss.TOTAL_TARGETS == 169
    assert len(ss.SDG_GOALS) == 17


def test_target_validation():
    assert ss.is_valid_target(16, "16.6")
    assert ss.is_valid_target(11, "11.b")
    assert ss.is_valid_target(17, "17.19")
    assert not ss.is_valid_target(17, "17.a")
    assert not ss.is_valid_target(7, "7.4")
    assert not ss.is_valid_target(3, "4.1")


def test_gemini_output_is_normalized_to_17_goals():
    payload = json.dumps({"goals": [
        {"goal": 16, "score": 9, "comment": "随意契約の理由が見えない", "targets": ["16.6", "16.10", "16.99"],
         "document_focus": "随意契約の理由書と決裁文書"},
        {"goal": 11, "score": 14, "comment": "公園を削るな", "targets": ["11.7"], "document_focus": "再開発の計画資料"},
        {"goal": 99, "score": 5},
    ]}, ensure_ascii=False)
    r = ss.score_sdgs("随意契約で公園を再開発", client=_FakeClient(payload))
    assert r["is_mock"] is False
    assert [g["goal"] for g in r["goals"]] == list(range(1, 18))
    g16 = r["goals"][15]
    assert g16["score"] == 9 and g16["targets"] == ["16.6", "16.10"]
    assert r["goals"][10]["score"] == 10  # 上限10に丸める
    assert r["top_goals"][:2] == [11, 16]
    assert "開示" not in r["disclosure_rationale"] or r["disclosure_rationale"]
    assert "16.6" in r["disclosure_rationale"]


def test_fallback_without_gemini(monkeypatch):
    class _Broken:
        models = None
    r = ss.score_sdgs("税金の使い道が不透明で随意契約ばかり", client=_Broken())
    assert r["is_mock"] is True
    assert r["top_goals"][0] == 16
    assert len(r["goals"]) == 17