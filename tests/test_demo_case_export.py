"""デモケースのエクスポート（ピッチ準備用）: 記録+音声を demo/cases/ 形式に書き出せること"""
import json

import pytest

import demo_case
import news_reactions
import voice_archive
import voice_panel


@pytest.fixture
def news_id(tmp_path, monkeypatch):
    """記録1件（summary つき）を一時ファイルに保存して返す"""
    monkeypatch.setattr(news_reactions, "_STORE_PATH", tmp_path / "records.json")
    # 同梱のサンプル記録（data/news_anger_records.json）を読み書きしない
    monkeypatch.setattr(news_reactions, "_BUNDLED_PATH", tmp_path / "bundled.json")
    link = "http://example.com/demo-case/1"
    nid = news_reactions.make_news_id(link)
    news_reactions.record_analysis(
        news_id=nid,
        source_news={"title": "アジア大会が閉幕 警備に多数の警察官", "link": link, "published": None,
                     "summary": "式典と大規模警備が行われた"},
        key_points=["警備費の内訳"],
        pseudo_citizen_voice="警備費の内訳を開示してください。",
        disclaimer="この声はAIが生成したフィクションです。",
        anger_analysis=None,
        theme="multi",
        region="名古屋市",
        voices=[{"theme": "sdg16", "label": "目標16 平和と公正", "remark": "r", "anger_level": 7,
                 "key_points": ["警備費の内訳"], "pseudo_citizen_voice": "警備費の内訳を開示してください。",
                 "guardrail": {"passed": True, "violations": []}}],
        proposals=[{"target_authority_key": "nagoya-city", "target_authority": "名古屋市",
                    "documents": ["支出負担行為決議裁許書"], "reason": "警備費の内訳を確認します。",
                    "supporting_themes": ["sdg16"]}],
        summary="警備費の使途が共通の懸念です。",
        overall_anger_level=7,
    )
    return nid


def test_export_case_writes_record_without_audio(news_id, tmp_path):
    out = demo_case.export_case(news_id, out_dir=tmp_path)
    assert out["audio"] is None
    data = json.loads(out["json"].read_text(encoding="utf-8"))
    assert data["case_id"] == news_id
    assert data["record"]["summary"] == "警備費の使途が共通の懸念です。"
    assert data["disclaimer"] == "この声はAIが生成したフィクションです。"
    assert data["voice"] is None


def test_export_case_keeps_news_summary_and_source(news_id, tmp_path):
    """記事のサマリーと出典（リンク）を残す。履歴からの再現・再分析に必要"""
    out = demo_case.export_case(news_id, out_dir=tmp_path)
    data = json.loads(out["json"].read_text(encoding="utf-8"))
    assert data["record"]["source_news"]["summary"] == "式典と大規模警備が行われた"
    assert data["record"]["source_news"]["link"] == "http://example.com/demo-case/1"


def test_export_case_includes_voice_script_and_timing(news_id, tmp_path):
    """voice_archive に音声があるとき、台本・ハッシュ・発話区間つきで Opus も書き出す"""
    record = news_reactions.get_record(news_id)
    wav = voice_panel._to_wav([b"\x00\x00" * 4800])
    entry = voice_archive.save(news_id, record, wav)

    out = demo_case.export_case(news_id, out_dir=tmp_path)
    assert out["audio"] is not None and out["audio"].read_bytes()
    data = json.loads(out["json"].read_text(encoding="utf-8"))
    assert data["voice"]["archive_id"] == entry["archive_id"]
    assert data["voice"]["filename"] == f"{news_id}.opus"
    assert data["voice"]["text_sha256"] == entry["text_sha256"]
    assert data["voice"]["script"][0]["text"] == voice_panel.OPENING  # 冒頭は必ず免責の読み上げ
    assert data["voice"]["timing"] and data["voice"]["timing"][0]["start"] == 0.0


def test_export_case_missing_record(tmp_path):
    with pytest.raises(KeyError):
        demo_case.export_case("missing-id", out_dir=tmp_path)


def test_list_cases_reports_progress(news_id, tmp_path, monkeypatch):
    rows = demo_case.list_cases()
    assert len(rows) == 1
    row = rows[0]
    assert row["news_id"] == news_id and row["voices"] == 1 and row["proposals"] == 1
    assert row["has_summary"] is True and row["has_request_detail"] is False
    assert row["has_audio"] is False  # 音声を生成するまでは False（--with-audio で生成）
