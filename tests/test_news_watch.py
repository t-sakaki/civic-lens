"""ニュースの見張り: 購読・通知のルール（オプトイン・1日の上限・夜間停止）と、定期実行の認証・通知の流れ"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import agent
import app as app_module
import news_reactions
import news_watch

client = TestClient(app_module.app)

DAY = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)  # 12:00 JST
NIGHT = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)  # 23:00 JST


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # Gemini通信なし（費用をかけず、結果を安定させる）
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))
    monkeypatch.setattr(news_watch, "use_firestore", lambda: False)
    monkeypatch.setattr(news_watch, "_STORE_PATH", tmp_path / "subs.json")
    monkeypatch.setattr(news_reactions, "_STORE_PATH", tmp_path / "records.json")
    app_module._APPEARANCE_CACHE.clear()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BPublicKeyForTest")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-for-test")
    monkeypatch.setenv("NEWS_AGENT_SCHEDULER_SECRET", "sched-secret")


def _subscription():
    return {"endpoint": f"https://push.example.com/{uuid.uuid4().hex}", "keys": {"p256dh": "p", "auth": "a"}}


def test_subscribe_validates_and_keeps_history_on_resubscribe():
    with pytest.raises(ValueError):
        news_watch.subscribe({"endpoint": "http://insecure", "keys": {}}, "名古屋市")
    sub = _subscription()
    first = news_watch.subscribe(sub, "名古屋市")
    news_watch.mark_notified(first, "n1", DAY)
    again = news_watch.subscribe(sub, "安城市")
    assert again["region"] == "安城市" and again["notified_ids"] == ["n1"]  # 地域だけ更新、履歴は引き継ぐ
    assert news_watch.unsubscribe(sub["endpoint"]) is True
    assert news_watch.unsubscribe(sub["endpoint"]) is False


def test_quiet_hours_daily_cap_and_dedupe(monkeypatch):
    monkeypatch.setenv("WATCH_DAILY_CAP", "2")
    sub = news_watch.subscribe(_subscription(), "名古屋市")
    assert news_watch.in_quiet_hours(NIGHT) and not news_watch.in_quiet_hours(DAY)
    assert not news_watch.can_notify(sub, "a", NIGHT)  # 夜間は送らない
    assert news_watch.can_notify(sub, "a", DAY)
    sub = news_watch.mark_notified(sub, "a", DAY)
    assert not news_watch.can_notify(sub, "a", DAY)  # 同じニュースは1回だけ
    sub = news_watch.mark_notified(sub, "b", DAY)
    assert not news_watch.can_notify(sub, "c", DAY)  # 1日の上限
    next_day = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)
    assert news_watch.can_notify(sub, "c", next_day)  # 翌日は再び通知できる


def test_payload_marks_ai_generated_and_not_a_real_citizen():
    p = news_watch.build_payload("警備費が未公表", "http://x/1", "目標16 平和と公正をすべての人に", "あ" * 200, 8)
    assert "AI" in p["title"] and "SDG16" in p["title"]
    assert "実在の市民の声ではありません" in p["body"] and len(p["body"]) < 200


def test_push_config_subscribe_and_unsubscribe_endpoints(monkeypatch):
    cfg = client.get("/api/push/config").json()
    assert cfg["enabled"] is True and cfg["public_key"] == "BPublicKeyForTest" and cfg["daily_cap"] == 3
    sub = _subscription()
    assert client.post("/api/push/subscribe", json={"subscription": sub, "region": "名古屋市"}).status_code == 200
    assert client.post("/api/push/subscribe", json={"subscription": sub, "region": " "}).status_code == 400
    assert client.post("/api/push/subscribe", json={"subscription": {"endpoint": "x"}, "region": "名古屋市"}).status_code == 400
    assert client.post("/api/push/unsubscribe", json={"endpoint": sub["endpoint"]}).json()["removed"] is True
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    assert client.get("/api/push/config").json()["enabled"] is False
    assert client.post("/api/push/subscribe", json={"subscription": sub, "region": "名古屋市"}).status_code == 503


def test_watch_and_scan_endpoints_fail_closed_without_secret(monkeypatch):
    monkeypatch.delenv("NEWS_AGENT_SCHEDULER_SECRET")
    assert client.post("/api/push/watch").status_code == 503  # 未設定のときは誰にも開放しない
    assert client.post("/api/news-agent/autonomous-scan", params={"regions": "名古屋市"}).status_code == 503
    monkeypatch.setenv("NEWS_AGENT_SCHEDULER_SECRET", "sched-secret")
    assert client.post("/api/push/watch").status_code == 401
    assert client.post("/api/push/watch", headers={"X-Scheduler-Secret": "wrong"}).status_code == 401


def test_watch_notifies_subscriber_once_per_news_with_replayable_record(monkeypatch):
    link = f"http://example.com/news/{uuid.uuid4().hex}"

    class _Item:
        title, summary, published = "アジア大会の警備費が未公表", "警備と交通規制", None

        def __init__(self):
            self.link = link

        def as_text(self):
            return f"{self.title}\n{self.summary}"

    class _Collector:
        def fetch_news(self, region, extra_keywords=None, max_items=5):
            return [_Item()]

    monkeypatch.setattr(app_module, "get_news_collector_agent", lambda: _Collector())
    top = {"theme": "sdg16", "label": "目標16 平和と公正をすべての人に", "remark": "警備費の使途が見えません。", "anger_level": 8}
    monkeypatch.setattr(app_module, "_get_appearances", lambda nid, text, region: [top])
    sent = []
    monkeypatch.setattr(news_watch, "send", lambda sub, payload: sent.append(payload) or "sent")
    sub = news_watch.subscribe(_subscription(), "名古屋市")

    result = app_module._run_watch(DAY)
    assert result["sent"] == 1 and len(sent) == 1
    assert sent[0]["link"] == link and "AI" in sent[0]["title"]
    # 通知の先で見せる検討が保存されている（再分析せず再生できる）
    assert news_reactions.get_record(news_reactions.make_news_id(link))["voices"]
    # 同じニュースは再通知しない・夜間は送らない
    assert app_module._run_watch(DAY)["sent"] == 0
    assert app_module._run_watch(NIGHT)["quiet"] is True and len(sent) == 1
    assert news_watch.get(sub["sub_id"])["day_count"] == 1


def test_watch_removes_expired_subscription(monkeypatch):
    class _Item:
        title, summary, published, link = "t", "s", None, "http://example.com/news/expired"

        def as_text(self):
            return "t\ns"

    class _Collector:
        def fetch_news(self, region, extra_keywords=None, max_items=5):
            return [_Item()]

    monkeypatch.setattr(app_module, "get_news_collector_agent", lambda: _Collector())
    monkeypatch.setattr(app_module, "_get_appearances", lambda nid, text, region: [
        {"theme": "sdg16", "label": "目標16 x", "remark": "r", "anger_level": 9}])
    monkeypatch.setattr(app_module, "_ensure_watch_record", lambda item, region: {})
    sub = news_watch.subscribe(_subscription(), "名古屋市")

    def _gone(s, p):
        news_watch.unsubscribe(s["endpoint"])
        return "gone"

    monkeypatch.setattr(news_watch, "send", _gone)
    assert app_module._run_watch(DAY)["gone"] == 1
    assert news_watch.get(sub["sub_id"]) is None
