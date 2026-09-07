import json
import pytest
import gmi_client


def test_search_ordinances_without_key_is_flagged_as_mock(monkeypatch):
    monkeypatch.setattr(gmi_client, "GMI_API_KEY", "")
    results = gmi_client.search_ordinances("海外視察費")
    assert len(results) > 0
    assert all(r.is_mock for r in results)


def test_search_precedents_without_key_is_flagged_as_mock(monkeypatch):
    monkeypatch.setattr(gmi_client, "GMI_API_KEY", "")
    results = gmi_client.search_precedents("契約金額 開示")
    assert len(results) > 0
    assert all(r.is_mock for r in results)


def test_search_precedents_uses_real_api_response_when_available(monkeypatch):
    """GMI_API_KEY設定時、実レスポンスの内容が反映され is_mock=False になること

    (過去のバグ: 実レスポンスを無視して常にモックを返していた)
    """
    monkeypatch.setattr(gmi_client, "GMI_API_KEY", "dummy-key-for-test")

    fake_payload = {
        "results": [
            {
                "title": "テスト判例",
                "source": "テスト高裁",
                "date": "2026-01-01",
                "relevance_score": 0.99,
                "summary": "これは実APIレスポンスに由来するテストデータです",
                "url": None,
            }
        ]
    }

    def fake_call_gmi(system_prompt, query):
        return json.dumps(fake_payload)

    monkeypatch.setattr(gmi_client, "_call_gmi", fake_call_gmi)

    results = gmi_client.search_precedents("テストクエリ")
    assert len(results) == 1
    assert results[0].title == "テスト判例"
    assert results[0].is_mock is False


def test_search_ordinances_falls_back_to_mock_on_api_error(monkeypatch):
    monkeypatch.setattr(gmi_client, "GMI_API_KEY", "dummy-key-for-test")

    def failing_call_gmi(system_prompt, query):
        raise RuntimeError("network error")

    monkeypatch.setattr(gmi_client, "_call_gmi", failing_call_gmi)

    results = gmi_client.search_ordinances("テストクエリ")
    assert len(results) > 0
    assert all(r.is_mock for r in results)
