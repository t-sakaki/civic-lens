"""ニュース1件ごとの複数エージェントの声の統合 → 開示請求対象の提案（Geminiなしのフォールバック経路）"""
import pytest
from fastapi.testclient import TestClient

import agent
import app as app_module
import news_anger_agent as naa
import news_reactions

client = TestClient(app_module.app)

NEWS = {
    "title": "アジア大会が成功裏に閉幕 警備に多数の警察官 交通規制も",
    "link": "http://example.com/news/1",
    "summary": "式典と大規模警備が行われた",
    "region": "名古屋市",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # Gemini通信なし・記録は一時ファイルへ
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))
    monkeypatch.setattr(news_reactions, "_STORE_PATH", tmp_path / "records.json")
    app_module._APPEARANCE_CACHE.clear()


def test_analyze_aggregates_multiple_agent_voices_and_proposes_targets():
    res = client.post("/api/news-agent/analyze", data=NEWS)
    assert res.status_code == 200
    record = res.json()

    # 記録IDはニュース1件ごと（エージェント別ではない）
    assert record["news_id"] == news_reactions.make_news_id(NEWS["link"])
    assert record["theme"] == "multi"
    # 交通規制・警察・式典 → 複数のSDGsエージェントが名乗り出て、それぞれ声を挙げる
    themes = [v["theme"] for v in record["voices"]]
    assert len(themes) >= 2 and len(set(themes)) == len(themes)
    for v in record["voices"]:
        assert v["key_points"] and v["pseudo_citizen_voice"]
    # 統合エージェントが開示請求の対象を提案する（機関は条例DBに存在するキー）
    assert record["proposals"]
    for p in record["proposals"]:
        assert app_module.get_ordinance(p["target_authority_key"]) is not None
        assert p["documents"]
    assert record["summary"]
    # 開示請求の詳細分析は提案を選ぶまで行わない。SNSシェア用の声は既存項目に残る
    assert record["anger_analysis"] is None
    assert record["pseudo_citizen_voice"]


def test_select_proposal_runs_disclosure_analysis_and_stores_it():
    record = client.post("/api/news-agent/analyze", data=NEWS).json()
    res = client.post(
        "/api/news-agent/select-proposal",
        data={"news_id": record["news_id"], "proposal_index": 0},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["selected_proposal"] == 0
    assert updated["anger_analysis"]["target_authority_key"]
    assert news_reactions.get_record(record["news_id"])["selected_proposal"] == 0


def test_select_proposal_validation():
    assert client.post(
        "/api/news-agent/select-proposal", data={"news_id": "missing", "proposal_index": 0}
    ).status_code == 404
    record = client.post("/api/news-agent/analyze", data=NEWS).json()
    assert client.post(
        "/api/news-agent/select-proposal", data={"news_id": record["news_id"], "proposal_index": 99}
    ).status_code == 400


def test_normalize_proposals_fixes_invalid_authority_and_unknown_supporters():
    voices = [{"theme": "sdg16", "label": "目標16", "key_points": ["a"], "pseudo_citizen_voice": "b"}]
    raw = [
        {"target_authority_key": "not-a-real-key", "documents": ["契約書"], "reason": "r",
         "supporting_themes": ["sdg16", "sdg99"]},
        {"target_authority_key": "nagoya-city", "documents": []},  # 文書なしは捨てる
        "not-a-dict",
    ]
    result = naa._normalize_proposals(raw, voices, hint_key="nagoya-city", news_text="t", region=None)
    assert len(result) == 1
    assert result[0]["target_authority_key"] == "nagoya-city"  # 不正キーはヒントで補正
    assert result[0]["supporting_themes"] == ["sdg16"]  # 存在しないエージェントIDは除外


def test_autonomous_scan_stores_one_record_per_news(monkeypatch):
    class _Item:
        title, link, published = NEWS["title"], NEWS["link"], None
        def as_text(self):
            return f"{NEWS['title']}\n{NEWS['summary']}"

    class _Collector:
        def fetch_news(self, region, extra_keywords=None, max_items=5):
            return [_Item()]

    monkeypatch.setattr(app_module, "get_news_collector_agent", lambda: _Collector())
    monkeypatch.setattr(app_module, "ANGER_LEVEL_AUTOSCAN_THRESHOLD", 1)
    res = client.post("/api/news-agent/autonomous-scan", params={"regions": "名古屋市"})
    assert res.status_code == 200
    body = res.json()
    assert body["created_count"] == 1 and not body["errors"]
    rec = body["created"][0]
    assert rec["autonomous"] is True and rec["read"] is False and len(rec["voices"]) >= 2
    # 同じ記事は再スキャンしない
    again = client.post("/api/news-agent/autonomous-scan", params={"regions": "名古屋市"}).json()
    assert again["created_count"] == 0


def test_ticker_endpoint_returns_agent_remarks_for_input_placeholder(monkeypatch):
    from news_collector_agent import NewsItem

    class _Collector:
        calls = 0

        def fetch_news(self, region, extra_keywords=None, max_items=5):
            _Collector.calls += 1
            return [
                NewsItem(title=NEWS["title"], link="http://example.com/t1", published=None, summary=NEWS["summary"]),
                NewsItem(title="市が新庁舎の入札結果を公表 随意契約に疑問の声", link="http://example.com/t2", published=None, summary="契約"),
            ]

    monkeypatch.setattr(app_module, "get_news_collector_agent", lambda: _Collector())
    app_module._TICKER_CACHE.clear()
    res = client.get("/api/news-agent/ticker", params={"region": "名古屋市"})
    assert res.status_code == 200
    body = res.json()
    assert body["items"]
    assert "実在の市民の声ではありません" in body["disclaimer"]  # AI生成であることを必ず明示
    for it in body["items"]:
        assert it["theme"].startswith("sdg") and it["remark"] and it["title"] and it["link"]
    # 複数の記事にまたがって並ぶ
    assert len({it["news_id"] for it in body["items"]}) == 2
    # 同じ地域は短時間キャッシュされ、ニュースを再取得しない
    client.get("/api/news-agent/ticker", params={"region": "名古屋市"})
    assert _Collector.calls == 1
