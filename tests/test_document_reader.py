from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import cost_guard
import document_reader as dr
import voice_panel

NOTICE = "一般質問通告書（愛知県議会）\n\n1. あいこんナビの個人情報誤掲載について（答弁者: 福祉局長）\n  (1) 発覚の経緯と県の対応を伺う。\n  (2) 再発防止策を伺う。"
SCRIPT = "議長のお許しをいただきましたので、通告に従い質問いたします。" * 10


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    cost_guard.reset_state()
    dr._cache.clear()
    yield
    cost_guard.reset_state()


@pytest.fixture
def tts(monkeypatch):
    calls = []

    def fake_line(client, voice, text):
        calls.append((voice, text))
        return b"\x01\x00" * 100

    monkeypatch.setattr(voice_panel, "_synthesize_line", fake_line)
    return calls


def test_split_text_breaks_at_sentence_ends_and_respects_limit():
    chunks = dr.split_text(SCRIPT, limit=100)
    assert len(chunks) > 1 and all(len(c) <= 100 + 30 for c in chunks)
    assert "".join(chunks) == SCRIPT, "文面は欠けずに分割される"
    assert all(c.endswith("。") for c in chunks[:-1]), "文の途中では切らない"


def test_split_text_handles_a_long_sentence_without_punctuation():
    chunks = dr.split_text("あ、" * 300, limit=100)
    assert len(chunks) > 1 and "".join(chunks) == "あ、" * 300


def test_prepare_always_starts_with_the_ai_disclaimer():
    for kind in ("notice", "script"):
        script = dr.prepare(NOTICE, kind)
        first = script[0]["text"]
        assert "AIが作成した" in first and "議員本人の発言ではありません" in first and "議員自身が行います" in first
        assert dr.KINDS[kind] in first
        assert script[0]["voice"] == voice_panel.NARRATOR_VOICE and script[1]["voice"] == dr.VOICE


@pytest.mark.parametrize("text,kind,exc", [
    ("", "notice", ValueError), ("   \n", "script", ValueError), (NOTICE, "other", ValueError),
    ("あ" * (dr.MAX_CHARS + 1), "script", dr.DocumentTooLong),
])
def test_prepare_rejects_bad_input(text, kind, exc):
    with pytest.raises(exc):
        dr.prepare(text, kind)


def test_prepare_rejects_too_many_chunks(monkeypatch):
    monkeypatch.setattr(dr, "split_text", lambda text, limit=dr.CHUNK_CHARS: ["a"] * dr.MAX_CHUNKS)
    with pytest.raises(dr.DocumentTooLong):
        dr.prepare("本文", "notice")


def test_synthesize_calls_tts_once_per_line_with_disclaimer_first_and_caches(tts):
    wav = dr.synthesize_document(object(), SCRIPT, "script")
    assert wav[:4] == b"RIFF"
    assert tts[0][1].startswith("これはAIが作成した登壇時の読み上げ原稿の案です")
    assert len(tts) == len(dr.prepare(SCRIPT, "script"))
    n = len(tts)
    assert dr.synthesize_document(object(), SCRIPT, "script") == wav and len(tts) == n, "同じ本文はキャッシュ"


# --- API --------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch, tts):
    import app as appmod

    monkeypatch.setattr(appmod, "get_agent", lambda: SimpleNamespace(genai_client=object()))
    return TestClient(appmod.app), appmod


def test_api_returns_wav_with_disclaimer(api, tts):
    c, _ = api
    r = c.post("/api/tts/document", data={"text": NOTICE, "kind": "notice"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert "議員本人の発言ではありません" in tts[0][1]


@pytest.mark.parametrize("data,status", [
    ({"text": "", "kind": "notice"}, 422),  # 必須項目の欠落はFastAPIが弾く
    ({"text": "  \n ", "kind": "notice"}, 400),
    ({"text": NOTICE, "kind": "x"}, 400),
    ({"text": "あ" * (dr.MAX_CHARS + 1), "kind": "script"}, 413),
])
def test_api_validates_input_without_calling_tts(api, tts, data, status):
    c, _ = api
    assert c.post("/api/tts/document", data=data).status_code == status
    assert not tts


def test_api_falls_back_with_503_when_gemini_missing(api, monkeypatch):
    c, appmod = api
    monkeypatch.setattr(appmod, "get_agent", lambda: SimpleNamespace(genai_client=None))
    r = c.post("/api/tts/document", data={"text": NOTICE, "kind": "notice"})
    assert r.status_code == 503 and "ブラウザの音声" in r.json()["detail"]


def test_api_returns_503_when_daily_tts_budget_is_exhausted(api, monkeypatch):
    c, _ = api
    monkeypatch.setenv("TTS_DAILY_CALL_LIMIT", "1")  # 免責＋本文で2回以上必要
    monkeypatch.setattr(voice_panel, "_synthesize_line", lambda client, voice, text: (cost_guard.consume("tts"), b"\x00\x00")[1])
    r = c.post("/api/tts/document", data={"text": NOTICE, "kind": "notice"})
    assert r.status_code == 503 and "上限" in r.json()["detail"]


def test_api_returns_503_on_synthesis_failure(api, monkeypatch):
    c, _ = api

    def boom(client, voice, text):
        raise RuntimeError("tts down")

    monkeypatch.setattr(voice_panel, "_synthesize_line", boom)
    r = c.post("/api/tts/document", data={"text": NOTICE, "kind": "notice"})
    assert r.status_code == 503 and "ブラウザの音声" in r.json()["detail"]


def test_index_page_has_read_aloud_controls(api):
    c, _ = api
    html = c.get("/").text
    assert "docSpeech" in html and "完成したら自動で読み上げる（既定はオフ）" in html and "/api/tts/document" in html
