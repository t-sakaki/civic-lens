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


def _wav(seconds=0.5, rate=24000):
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x10" * int(seconds * rate))
    return buf.getvalue()


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
    assert entry["audio_codec"] == "opus" and res.content[:4] == b"OggS"  # 既定はOpus
    audio, mime, ext = voice_archive.read_audio(entry)
    assert audio == res.content and ext == "opus" and mime.startswith("audio/ogg")  # 記録された音声が返したものと一致
    assert entry["audio_sha256"] == voice_archive._sha256(audio)  # ハッシュは保存したOpusファイルのもの


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
    assert client.get(f"/api/voice-archive/{aid}/audio", headers=ADMIN).content[:4] == b"OggS"

    ok = client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"})
    assert ok.status_code == 200 and ok.json()["status"] == "published"

    items = client.get("/api/voice-archive").json()["items"]
    assert [i["archive_id"] for i in items] == [aid]
    assert "audio_ref" not in items[0] and items[0]["disclaimer"] == voice_panel.OPENING
    assert client.get(f"/api/voice-archive/{aid}/audio").content[:4] == b"OggS"  # 公開後は誰でも再生できる
    assert client.get(f"/api/voice-archive/{aid}/audio?format=wav").content[:4] == b"RIFF"  # Opusを再生できない端末向け
    feed = client.get("/voices/feed.xml")
    assert aid in feed.text and "フィクション" in feed.text and feed.headers["content-type"].startswith("application/rss+xml")

    assert client.post(f"/api/voice-archive/admin/{aid}/unpublish", headers=ADMIN).json()["status"] == "private"
    assert client.get("/api/voice-archive").json()["items"] == []


def test_publish_is_refused_if_guardrail_violation_remains():
    bad = [{**VOICES[0], "pseudo_citizen_voice": "訴えれば勝てるはずです。"}]
    rec = _record("bad", bad)
    entry = voice_archive.save("bad", rec, _wav())
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
    assert res.headers["X-Voice-Filename"] == f"{name}.opus"
    assert f'filename="{name}.opus"' in res.headers["content-disposition"]
    assert res.headers["content-disposition"].startswith("inline")  # 再生は妨げない
    client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"})
    pub = client.get(f"/api/voice-archive/{aid}/audio")
    assert f'filename="{name}.opus"' in pub.headers["content-disposition"]
    wav = client.get(f"/api/voice-archive/{aid}/audio?format=wav")
    assert f'filename="{name}.wav"' in wav.headers["content-disposition"]


def test_voice_panel_can_return_wav_for_browsers_without_opus():
    rec = _record("wavfmt")
    res = client.post("/api/news-agent/voice-panel", data={"news_id": rec["news_id"], "format": "wav"})
    assert res.status_code == 200 and res.content[:4] == b"RIFF" and res.headers["content-type"] == "audio/wav"
    assert res.headers["X-Voice-Filename"].endswith(".wav")
    assert voice_archive.get(res.headers["X-Voice-Archive-Id"])["audio_codec"] == "opus"  # 記録は常にOpus


def test_audio_is_split_into_firestore_sized_chunks():
    data = bytes(range(256)) * 10_000  # 約2.5MB
    chunks = voice_archive.split_chunks(data)
    assert all(len(c) <= voice_archive.CHUNK_BYTES for c in chunks) and len(chunks) > 1
    assert b"".join(chunks) == data and voice_archive.split_chunks(b"") == [b""]
    assert voice_archive.CHUNK_BYTES < 1024 * 1024  # Firestoreの1ドキュメント上限(1MiB)未満


def test_missing_audio_blocks_publish_and_is_repaired_by_regenerating():
    first = _generate()
    aid = first.headers["X-Voice-Archive-Id"]
    entry = voice_archive.get(aid)
    # 旧版のように、記録は残っているが音声ファイルが消えた状態を再現
    (voice_archive._DIR / entry["audio_ref"].removeprefix("local:")).unlink()
    assert voice_archive.read_audio(entry) is None

    res = client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"})
    assert res.status_code == 409 and "音声ファイルが見つかりません" in res.json()["detail"]
    assert voice_archive.get(aid)["status"] == voice_archive.PRIVATE

    # 同じ台本で音声を作り直すと、同じ記録IDのまま音声が保存し直される（復旧）
    voice_panel._cache.clear()
    again = _generate()
    assert again.headers["X-Voice-Archive-Id"] == aid
    assert voice_archive.read_audio(voice_archive.get(aid))[0] == again.content
    assert len(voice_archive.list_all()) == 1
    assert voice_archive.get(aid)["generated_at"] == entry["generated_at"]  # 記録の日時は変わらない
    assert client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"}).status_code == 200


def test_admin_can_delete_record_and_audio_but_not_while_published():
    aid = _generate().headers["X-Voice-Archive-Id"]
    entry = voice_archive.get(aid)
    audio_file = voice_archive._DIR / entry["audio_ref"].removeprefix("local:")
    assert audio_file.exists()

    # 認証が必要
    assert client.delete(f"/api/voice-archive/admin/{aid}").status_code == 401
    assert client.delete(f"/api/voice-archive/admin/{aid}", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert voice_archive.get(aid) is not None

    # 公開中は削除できない
    client.post(f"/api/voice-archive/admin/{aid}/publish", headers=ADMIN, data={"confirmed": "true"})
    assert client.delete(f"/api/voice-archive/admin/{aid}", headers=ADMIN).status_code == 409
    assert voice_archive.get(aid) is not None and audio_file.exists()

    # 非公開に戻せば削除でき、記録も音声も消える
    client.post(f"/api/voice-archive/admin/{aid}/unpublish", headers=ADMIN)
    assert client.delete(f"/api/voice-archive/admin/{aid}", headers=ADMIN).json() == {"archive_id": aid, "deleted": True}
    assert voice_archive.get(aid) is None and not audio_file.exists()
    assert client.get("/api/voice-archive/admin/list", headers=ADMIN).json()["items"] == []
    assert client.delete(f"/api/voice-archive/admin/{aid}", headers=ADMIN).status_code == 404


def test_admin_list_flags_records_whose_audio_is_lost():
    ok = _generate("keep").headers["X-Voice-Archive-Id"]
    lost = _generate("lost").headers["X-Voice-Archive-Id"]
    (voice_archive._DIR / voice_archive.get(lost)["audio_ref"].removeprefix("local:")).unlink()
    items = {i["archive_id"]: i for i in client.get("/api/voice-archive/admin/list", headers=ADMIN).json()["items"]}
    assert items[ok]["audio_available"] is True and items[lost]["audio_available"] is False
    # 音声が失われた記録は削除できる（公開は拒否される）
    assert client.post(f"/api/voice-archive/admin/{lost}/publish", headers=ADMIN, data={"confirmed": "true"}).status_code == 409
    assert client.delete(f"/api/voice-archive/admin/{lost}", headers=ADMIN).status_code == 200
