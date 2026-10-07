"""音声アーカイブ: 常に記録（非公開）→ 運営者の承認で公開（Gemini/TTS/GCS通信なし）"""
import pytest
from fastapi.testclient import TestClient

import agent
import app as app_module
import cost_guard
import news_reactions
import voice_archive
import voice_panel

client = TestClient(app_module.app)
ADMIN = {"X-Admin-Token": "s3cret-token"}

VOICES = [
    {"theme": "sdg16", "label": "目標16 平和と公正をすべての人に", "anger_level": 8, "pseudo_citizen_voice": "警備の費用がどこにも出てこないのが気になります。"},
    {"theme": "sdg11", "label": "目標11 住み続けられるまちづくりを", "anger_level": 6, "pseudo_citizen_voice": "交通規制の説明が足りないと感じます。"},
]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: object()))
    monkeypatch.setattr(news_reactions, "_STORE_PATH", tmp_path / "records.json")
    monkeypatch.setattr(voice_archive, "_DIR", tmp_path / "archive")
    monkeypatch.delenv("VOICE_ARCHIVE_BUCKET", raising=False)
    monkeypatch.setenv("ARCHIVE_ADMIN_TOKEN", "s3cret-token")
    monkeypatch.setattr(voice_panel, "_synthesize_line", lambda c, v, t: b"\x01\x00" * 200)
    voice_panel._cache.clear()
    cost_guard.reset_state()
    yield
    cost_guard.reset_state()


def _record(news_id="n1", voices=VOICES):
    return news_reactions.record_analysis(
        news_id=news_id, source_news={"title": "アジア大会が閉幕", "link": f"http://example.com/{news_id}", "published": None},
        disclaimer="d", anger_analysis=None, theme="multi", region="名古屋市", autonomous=False,
        voices=voices, proposals=[{"target_authority": "愛知県警察本部"}], summary="警備費の使途が共通の懸念です。",
        overall_anger_level=7, key_points=["k"], pseudo_citizen_voice="v",
    )


def _generate(news_id="n1", voices=VOICES):
    rec = _record(news_id, voices)
    res = client.post("/api/news-agent/voice-panel", data={"news_id": rec["news_id"]})
    assert res.status_code == 200
    return res


def test_voice_panel_always_archives_privately_with_provenance():
    res = _generate()
    aid = res.headers["X-Voice-Archive-Id"]
    entry = voice_archive.get(aid)
    assert entry["status"] == voice_archive.PRIVATE and entry["published_at"] is None
    assert entry["source_news"]["link"] == "http://example.com/n1"
    assert entry["audio_sha256"] and entry["text_sha256"] and entry["tts_model"]
    assert entry["script"][0]["text"] == voice_panel.OPENING  # 冒頭の免責
    assert voice_archive.read_audio(entry) == res.content  # 記録された音声が生成したものと一致


def test_same_script_is_archived_once_and_snapshots_are_immutable():
    a1 = _generate("n1").headers["X-Voice-Archive-Id"]
    voice_panel._cache.clear()
    a2 = _generate("n1").headers["X-Voice-Archive-Id"]
    assert a1 == a2 and len(voice_archive.list_all()) == 1
    # 再分析で声が変わると、別のスナップショットとして記録される（公開済みの内容は変わらない）
    other = [{**VOICES[0], "pseudo_citizen_voice": "別の内容です。"}]
    voice_panel._cache.clear()
    a3 = _generate("n1", other).headers["X-Voice-Archive-Id"]
    assert a3 != a1 and len(voice_archive.list_all()) == 2


def test_private_items_are_not_public():
    aid = _generate().headers["X-Voice-Archive-Id"]
    assert client.get("/api/voice-archive").json()["items"] == []
    assert client.get(f"/api/voice-archive/{aid}/audio").status_code in (401, 503)
    assert aid not in client.get("/voices/feed.xml").text


def test_admin_endpoints_require_token(monkeypatch):
    aid = _generate().headers["X-Voice-Archive-Id"]
    assert client.get("/api/voice-archive/admin/list").status_code == 401
    assert client.get("/api/voice-archive/admin/list", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post(f"/api/voice-archive/admin/{aid}/publish", data={"confirmed": "true"}).status_code == 401
    monkeypatch.delenv("ARCHIVE_ADMIN_TOKEN")
    assert client.get("/api/voice-archive/admin/list", headers=ADMIN).status_code == 503  # 未設定なら承認機能は無効


def test_publish_requires_human_confirmation_then_goes_public():
    aid = _generate().headers["X-Voice-Archive-Id"]
    # 確認チェック（confirmed）がなければ公開できない
    assert client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN).status_code == 400
    assert voice_archive.get(aid)["status"] == voice_archive.PRIVATE

    # 運営者は未公開でも音声を確認できる
    assert client.get(f"/api/voice-archive/{aid}/audio", headers=ADMIN).content[:4] == b"RIFF"

    ok = client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"})
    assert ok.status_code == 200 and ok.json()["status"] == "published"

    items = client.get("/api/voice-archive").json()["items"]
    assert [i["archive_id"] for i in items] == [aid]
    assert "audio_ref" not in items[0] and items[0]["disclaimer"] == voice_panel.OPENING
    assert client.get(f"/api/voice-archive/{aid}/audio").content[:4] == b"RIFF"  # 公開後は誰でも再生できる
    feed = client.get("/voices/feed.xml")
    assert aid in feed.text and "フィクション" in feed.text and feed.headers["content-type"].startswith("application/rss+xml")

    assert client.post(f"/api/voice-archive/admin/{aid}/unpublish", headers=ADMIN).json()["status"] == "private"
    assert client.get("/api/voice-archive").json()["items"] == []


def test_publish_is_refused_if_guardrail_violation_remains():
    bad = [{**VOICES[0], "pseudo_citizen_voice": "訴えれば勝てるはずです。"}]
    rec = _record("bad", bad)
    entry = voice_archive.save("bad", rec, b"\x01\x00" * 10)
    # ガードレールは生成時に通るが、承認時にも再検査される（台本を直接差し込んで確認）
    entry["script"][1]["text"] = "これは違法です。訴えれば勝てる。"
    voice_archive._put(entry)
    res = client.post(f"/api/voice-archive/admin/{entry['archive_id']}/publish", headers=ADMIN, data={"confirmed": "true"})
    assert res.status_code == 422 and "legal_advice" in res.json()["detail"]
    assert voice_archive.get(entry["archive_id"])["status"] == voice_archive.PRIVATE


def test_pages_render():
    assert "AIが生成したフィクション" in client.get("/voices").text
    assert "承認して公開する" in client.get("/admin/voices").text


def test_download_filename_is_ascii_slug_of_record_id():
    res = _generate()
    aid = res.headers["X-Voice-Archive-Id"]
    entry = voice_archive.get(aid)
    name = voice_archive.slug(entry)
    assert name == f"civic-lens-{entry['generated_at'][:10].replace('-', '')}-{aid}"
    assert name.isascii() and " " not in name
    # 音声パネルのレスポンス・アーカイブの音声のどちらも、保存時のファイル名がスラッグになる
    assert res.headers["X-Voice-Filename"] == f"{name}.wav"
    assert f'filename="{name}.wav"' in res.headers["content-disposition"]
    assert res.headers["content-disposition"].startswith("inline")  # 再生は妨げない
    client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"})
    pub = client.get(f"/api/voice-archive/{aid}/audio")
    assert f'filename="{name}.wav"' in pub.headers["content-disposition"]
