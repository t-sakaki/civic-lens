"""ガードレール監視・費用ガード・音声パネル（Gemini/TTS通信なし）"""
import io
import wave

import pytest
from fastapi.testclient import TestClient

import agent
import app as app_module
import cost_guard
import gemini_models
import guardrail
import news_reactions
import voice_panel

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))
    monkeypatch.setattr(news_reactions, "_STORE_PATH", tmp_path / "records.json")
    app_module._APPEARANCE_CACHE.clear()
    cost_guard.reset_state()
    voice_panel._cache.clear()
    yield
    cost_guard.reset_state()


# --- ガードレール ---

@pytest.mark.parametrize("text,kind", [
    ("電話は052-123-4567です", guardrail.PERSONAL_INFO),
    ("連絡は taro@example.com まで", guardrail.PERSONAL_INFO),
    ("私の名前は山田です", guardrail.PERSONAL_INFO),
    ("これは違法です", guardrail.LEGAL_ADVICE),
    ("訴えれば勝てる", guardrail.LEGAL_ADVICE),
    ("住民のAさんは次のように語った", guardrail.IMPERSONATION),
])
def test_check_text_detects_violations(text, kind):
    assert kind in guardrail.check_text(text)


def test_check_text_passes_normal_voice():
    assert guardrail.check_text("警備費の内訳がどこにも出てこないのが気になります。") == []


def test_guard_voice_replaces_violating_voice_and_keeps_clean_one():
    bad = guardrail.guard_voice({"theme": "sdg1", "key_points": ["a"], "pseudo_citizen_voice": "訴えれば勝てる。電話は052-123-4567"})
    assert bad["pseudo_citizen_voice"] == guardrail.SAFE_VOICE
    assert not bad["guardrail"]["passed"]
    assert set(bad["guardrail"]["violations"]) == {guardrail.LEGAL_ADVICE, guardrail.PERSONAL_INFO}

    ok = guardrail.guard_voice({"theme": "sdg1", "key_points": ["a"], "pseudo_citizen_voice": "費用が気になります"})
    assert ok["pseudo_citizen_voice"] == "費用が気になります"
    assert ok["guardrail"] == {"passed": True, "violations": []}


def test_guard_proposal_result_sanitizes_reason():
    res = guardrail.guard_proposal_result({
        "summary": "必ず勝訴できる",
        "proposals": [{"reason": "違法です", "documents": ["契約書", "電話 052-111-2222 の記録"]}],
    })
    assert "勝訴" not in res["summary"]
    assert res["proposals"][0]["reason"] == guardrail.SAFE_PROPOSAL_REASON
    assert res["proposals"][0]["documents"] == ["契約書"]
    assert not res["guardrail"]["passed"]


def test_pipeline_voices_carry_guardrail_result():
    res = client.post("/api/news-agent/analyze", data={
        "title": "アジア大会が閉幕 警備と交通規制", "link": "http://example.com/g/1", "summary": "警察官が多数動員", "region": "名古屋市",
    })
    assert res.status_code == 200
    assert all(v["guardrail"]["passed"] for v in res.json()["voices"])


# --- 費用ガード ---

def test_daily_limit_blocks_gemini_calls(monkeypatch):
    monkeypatch.setenv("GEMINI_DAILY_CALL_LIMIT", "2")
    cost_guard.consume("gemini")
    cost_guard.consume("gemini")
    with pytest.raises(cost_guard.BudgetExceeded):
        cost_guard.consume("gemini")
    with pytest.raises(cost_guard.BudgetExceeded):
        gemini_models.generate(object(), "pro", "x")


def test_daily_limit_unlimited_by_default(monkeypatch):
    monkeypatch.delenv("GEMINI_DAILY_CALL_LIMIT", raising=False)
    for _ in range(50):
        cost_guard.consume("gemini")


def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    data = {"title": "t", "link": "http://example.com/r/1", "summary": "警察の警備", "region": "名古屋市"}
    assert client.post("/api/news-agent/appear", data=data).status_code == 200
    assert client.post("/api/news-agent/appear", data=data).status_code == 200
    assert client.post("/api/news-agent/appear", data=data).status_code == 429


# --- 音声パネル ---

RECORD = {
    "summary": "警備費の使途が共通の懸念です。",
    "proposals": [{"target_authority": "愛知県警察本部"}],
    "voices": [
        {"label": "目標16 平和と公正をすべての人に", "anger_level": 8, "pseudo_citizen_voice": "警備の費用が見えません。"},
        {"label": "目標11 住み続けられるまちづくりを", "anger_level": 6, "pseudo_citizen_voice": "交通規制の説明が足りません。"},
        {"label": "目標1 貧困をなくそう", "anger_level": 4, "pseudo_citizen_voice": "負担が気になります。"},
        {"label": "目標3 健康と福祉", "anger_level": 3, "pseudo_citizen_voice": "4体目は読み上げない。"},
    ],
}


def test_build_script_starts_with_disclaimer_and_caps_agents():
    script = voice_panel.build_script(RECORD)
    assert script[0]["text"] == voice_panel.OPENING and "フィクション" in script[0]["text"]
    agent_lines = [l for l in script if l["speaker"].startswith("AI・") and "統合" not in l["speaker"]]
    assert len(agent_lines) == voice_panel.MAX_AGENT_LINES
    assert "目標" not in agent_lines[0]["text"]  # 目標番号は読ませない
    assert agent_lines[0]["text"].startswith("AIの")  # AIであることを名乗る（怒りの強い順）
    assert len({l["voice"] for l in script}) == len(script)  # 話者ごとに別の声
    assert script[-1]["speaker"] == "AI・統合エージェント" and "判断はあなた自身" in script[-1]["text"]


def test_synthesize_panel_concatenates_wav_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(voice_panel, "_synthesize_line", lambda c, v, t: calls.append(v) or b"\x01\x00" * 100)
    wav = voice_panel.synthesize_panel(object(), "n1", RECORD)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == voice_panel.SAMPLE_RATE and w.getnchannels() == 1
    n = len(calls)
    voice_panel.synthesize_panel(object(), "n1", RECORD)
    assert len(calls) == n  # キャッシュ済みなら再合成しない


def test_voice_panel_endpoint_404_and_503():
    assert client.post("/api/news-agent/voice-panel", data={"news_id": "nope"}).status_code == 404
    rec = news_reactions.record_analysis(
        news_id="vp1", source_news={"title": "t", "link": "http://example.com/vp", "published": None},
        disclaimer="d", anger_analysis=None, theme="multi", region="名古屋市", autonomous=False,
        voices=RECORD["voices"], proposals=RECORD["proposals"], summary=RECORD["summary"],
        overall_anger_level=7, key_points=["k"], pseudo_citizen_voice="v",
    )
    # Gemini未設定ではテキスト表示のままにするため 503
    assert client.post("/api/news-agent/voice-panel", data={"news_id": rec["news_id"]}).status_code == 503


def test_voice_panel_endpoint_returns_opus_by_default_and_wav_on_request(monkeypatch):
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: object()))
    monkeypatch.setattr(voice_panel, "_synthesize_line", lambda c, v, t: b"\x01\x00" * 50)
    rec = news_reactions.record_analysis(
        news_id="vp2", source_news={"title": "t", "link": "http://example.com/vp2", "published": None},
        disclaimer="d", anger_analysis=None, theme="multi", region="名古屋市", autonomous=False,
        voices=RECORD["voices"], proposals=RECORD["proposals"], summary=RECORD["summary"],
        overall_anger_level=7, key_points=["k"], pseudo_citizen_voice="v",
    )
    # 既定はOpus（小さい）。Opusを再生できない端末向けには format=wav でWAVを返す
    res = client.post("/api/news-agent/voice-panel", data={"news_id": rec["news_id"]})
    assert res.status_code == 200 and res.headers["content-type"].startswith("audio/ogg")
    assert res.content[:4] == b"OggS"
    wav = client.post("/api/news-agent/voice-panel", data={"news_id": rec["news_id"], "format": "wav"})
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav" and wav.content[:4] == b"RIFF"


def test_agent_lines_match_spoken_script_and_are_exposed_on_analyze():
    lines = voice_panel.agent_lines(RECORD)
    script = voice_panel.build_script(RECORD)
    spoken = [l["text"] for l in script if l["speaker"].startswith("AI・") and "統合" not in l["speaker"]]
    # 画面に出す一言と、読み上げる本文が一致する
    assert [f"AIの{l['label']}担当です。{l['body']}" for l in lines] == spoken
    assert all(len(t) <= voice_panel.MAX_LINE_CHARS for t in spoken)

    res = client.post("/api/news-agent/analyze", data={
        "title": "アジア大会が閉幕 警備と交通規制", "link": "http://example.com/pl/1", "summary": "警察官が多数動員", "region": "名古屋市",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["panel_script"] and all(l["label"] and l["body"] for l in body["panel_script"])
    assert "panel_script" not in news_reactions.get_record(body["news_id"])  # 保存はしない


def test_clip_cuts_at_sentence_boundary():
    text = "最初の文です。" * 30
    clipped = voice_panel._clip(text, 40)
    assert len(clipped) <= 40 and clipped.endswith("。") and "…" not in clipped
    assert voice_panel._clip("短い文。", 40) == "短い文。"
    assert voice_panel._clip("句点のない" * 20, 40).endswith("…")
    assert voice_panel._clip("すごい！" * 30, 40).endswith("！")
