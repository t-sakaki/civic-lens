"""ニュース1件ごとの複数エージェントの声の統合 → 開示請求対象の提案（Geminiなしのフォールバック経路）"""
import uuid

import pytest
from fastapi.testclient import TestClient

import agent
import app as app_module
import news_anger_agent as naa
import news_reactions

client = TestClient(app_module.app)

NEWS_BASE = {
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


@pytest.fixture
def news():
    """テストごとに記事URLを一意にする。

    CIはFirestoreエミュレータ上で動き記録がテスト間で残るため、同じURLを使い回すと
    「分析済みの記事」として自律スキャン等がスキップされてしまう。
    """
    return {**NEWS_BASE, "link": f"http://example.com/news/{uuid.uuid4().hex}"}


def test_analyze_aggregates_multiple_agent_voices_and_proposes_targets(news):
    res = client.post("/api/news-agent/analyze", data=news)
    assert res.status_code == 200
    record = res.json()

    # 記録IDはニュース1件ごと（エージェント別ではない）
    assert record["news_id"] == news_reactions.make_news_id(news["link"])
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


def test_select_proposal_runs_disclosure_analysis_and_stores_it(news):
    record = client.post("/api/news-agent/analyze", data=news).json()
    res = client.post(
        "/api/news-agent/select-proposal",
        data={"news_id": record["news_id"], "proposal_index": 0},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["selected_proposal"] == 0
    assert updated["anger_analysis"]["target_authority_key"]
    assert news_reactions.get_record(record["news_id"])["selected_proposal"] == 0


def test_select_proposal_validation(news):
    assert client.post(
        "/api/news-agent/select-proposal", data={"news_id": "missing", "proposal_index": 0}
    ).status_code == 404
    record = client.post("/api/news-agent/analyze", data=news).json()
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


def test_autonomous_scan_stores_one_record_per_news(monkeypatch, news):
    class _Item:
        title, link, published = news["title"], news["link"], None
        def as_text(self):
            return f"{news['title']}\n{news['summary']}"

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


def test_ticker_endpoint_returns_agent_remarks_for_input_placeholder(monkeypatch, news):
    from news_collector_agent import NewsItem

    class _Collector:
        calls = 0

        def fetch_news(self, region, extra_keywords=None, max_items=5):
            _Collector.calls += 1
            return [
                NewsItem(title=news["title"], link=news["link"] + "/t1", published=None, summary=news["summary"]),
                NewsItem(title="市が新庁舎の入札結果を公表 随意契約に疑問の声", link=news["link"] + "/t2", published=None, summary="契約"),
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


def test_proposals_are_merged_into_one_per_authority():
    voices = [{"theme": "sdg16"}, {"theme": "sdg11"}, {"theme": "sdg1"}]
    raw = [
        {"target_authority_key": "anjo-city", "documents": ["契約書", "仕様書"], "reason": "警備費の内訳が不明です。", "supporting_themes": ["sdg16"]},
        {"target_authority_key": "nagoya-city", "documents": ["議事録"], "reason": "意思決定の経緯を確認します。", "supporting_themes": ["sdg1"]},
        # 同じ機関に、別の担当のエージェントが別の文書・理由で提案した → 1件に集約される
        {"target_authority_key": "anjo-city", "documents": ["仕様書", "支出負担行為決議書"], "reason": "交通規制の費用を確認します。", "supporting_themes": ["sdg11", "sdg16"]},
    ]
    merged = naa._normalize_proposals(raw, voices, None, "警備と交通規制", "安城市")
    assert [p["target_authority_key"] for p in merged] == ["anjo-city", "nagoya-city"]  # 初出順を保つ
    anjo = merged[0]
    assert anjo["documents"] == ["契約書", "仕様書", "支出負担行為決議書"]  # 重複を除いて合算
    assert anjo["supporting_themes"] == ["sdg16", "sdg11"]  # 担当エージェントを合算
    assert "警備費の内訳が不明です。" in anjo["reason"] and "交通規制の費用を確認します。" in anjo["reason"]


def test_merged_documents_are_capped():
    voices = [{"theme": "sdg16"}]
    raw = [{"target_authority_key": "anjo-city", "documents": [f"文書{i}" for i in range(6)], "reason": "a。", "supporting_themes": []},
           {"target_authority_key": "anjo-city", "documents": [f"別{i}" for i in range(6)], "reason": "b。", "supporting_themes": []}]
    merged = naa._normalize_proposals(raw, voices, None, "x", "安城市")
    assert len(merged) == 1 and len(merged[0]["documents"]) == naa.MAX_DOCUMENTS_PER_PROPOSAL
