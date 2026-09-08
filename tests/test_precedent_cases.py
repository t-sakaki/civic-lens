import sys

import pytest
from fastapi.testclient import TestClient

from app import app
import agent
import precedent_cases


client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_genai_disabled(monkeypatch):
    """テスト実行時は外部Gemini API通信をモック化して高速化・安定化"""
    monkeypatch.setattr(agent.CivicLensAgent, "genai_client", property(lambda self: None))


def test_gyofuku_cases_data_exists_and_is_well_formed():
    cases = precedent_cases.list_cases()
    assert len(cases) > 0
    required_fields = {
        "case_id", "category", "authority", "basis_laws", "decision_date",
        "result", "summary", "source_url", "attribution",
    }
    for c in cases:
        assert required_fields.issubset(c.keys())
        # 認容・一部認容のみを蓄積している前提
        assert "認容" in c["result"]
        # 出典URL・出典表記が必ず含まれている（PDL1.0の出典明記義務対応）
        assert c["source_url"].startswith("https://fufukudb.search.soumu.go.jp/")
        assert "出典" in c["attribution"]
        # 全文ではなく概要スニペットのみを保持している（過度に長い全文を保存していないことの簡易チェック）
        assert len(c["summary"]) < 500


def test_search_cases_by_query():
    results = precedent_cases.search_cases(query="情報公開", limit=10)
    assert isinstance(results, list)
    if results:
        assert all("情報公開" in (c["authority"] + c["basis_laws"] + c["summary"] + c.get("council_name", "")) for c in results)


def test_search_cases_by_category():
    results = precedent_cases.search_cases(category="裁決", limit=100)
    assert all(c["category"] == "裁決" for c in results)


def test_get_case_roundtrip():
    cases = precedent_cases.list_cases()
    assert cases, "テスト用データが存在しません"
    target = cases[0]
    fetched = precedent_cases.get_case(target["case_id"])
    assert fetched == target


def test_get_case_missing_returns_none():
    assert precedent_cases.get_case("does-not-exist") is None


def test_find_relevant_precedents_returns_real_data_when_matching():
    cases = precedent_cases.list_cases()
    assert cases
    sample = cases[0]
    results = precedent_cases.find_relevant_precedents(
        ordinance_name=sample["basis_laws"],
        alleged_ground=sample["summary"],
        authority=sample["authority"],
        top_k=3,
    )
    assert len(results) >= 1
    assert any(r["case_id"] == sample["case_id"] for r in results)


def test_find_relevant_precedents_no_match_returns_empty():
    results = precedent_cases.find_relevant_precedents(
        ordinance_name="全く関係のない架空条例XYZ999",
        alleged_ground="無関係な理由ZZZ000",
        authority="存在しない機関AAA",
    )
    assert results == []


def test_format_precedent_case_for_prompt_includes_attribution():
    cases = precedent_cases.list_cases()
    assert cases
    text = precedent_cases.format_precedent_case_for_prompt(cases[0])
    assert cases[0]["attribution"] in text


def test_precedent_cases_page_renders():
    res = client.get("/precedent-cases")
    assert res.status_code == 200
    assert "認容事例" in res.text


def test_api_precedent_cases_list():
    res = client.get("/api/precedent-cases")
    assert res.status_code == 200
    data = res.json()
    assert "cases" in data
    assert data["count"] == len(data["cases"])


def test_api_precedent_cases_filter_by_result():
    res = client.get("/api/precedent-cases", params={"result": "一部認容"})
    assert res.status_code == 200
    data = res.json()
    assert all("一部認容" in c["result"] for c in data["cases"])


def test_api_precedent_case_detail_not_found():
    res = client.get("/api/precedent-cases/does-not-exist")
    assert res.status_code == 404


def test_api_precedent_case_detail_found():
    cases = precedent_cases.list_cases()
    assert cases
    case_id = cases[0]["case_id"]
    res = client.get(f"/api/precedent-cases/{case_id}")
    assert res.status_code == 200
    assert res.json()["case_id"] == case_id


def test_cosine_similarity_identical_vectors_is_one():
    v = [0.1, 0.2, 0.3, 0.4]
    assert precedent_cases._cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert precedent_cases._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_handles_empty_or_mismatched_vectors():
    assert precedent_cases._cosine_similarity([], [1.0]) == 0.0
    assert precedent_cases._cosine_similarity([1.0, 2.0], [1.0]) == 0.0
    assert precedent_cases._cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_find_relevant_precedents_by_embedding_falls_back_without_api_key(monkeypatch):
    """GEMINI_API_KEY未設定時はNgram検索にフォールバックする。"""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cases = precedent_cases.list_cases()
    assert cases
    sample = cases[0]
    results = precedent_cases.find_relevant_precedents_by_embedding(
        ordinance_name=sample["basis_laws"],
        alleged_ground=sample["summary"],
        authority=sample["authority"],
        top_k=3,
    )
    assert len(results) >= 1
    assert any(r["case_id"] == sample["case_id"] for r in results)


def test_find_relevant_precedents_by_embedding_falls_back_without_precomputed_vectors(monkeypatch):
    """埋め込みデータ(data/gyofuku_cases_embeddings.json)が無い場合もNgram検索にフォールバックする。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr(precedent_cases, "_load_embeddings", lambda: {})
    cases = precedent_cases.list_cases()
    assert cases
    sample = cases[0]
    results = precedent_cases.find_relevant_precedents_by_embedding(
        ordinance_name=sample["basis_laws"],
        alleged_ground=sample["summary"],
        authority=sample["authority"],
        top_k=3,
    )
    assert len(results) >= 1
    assert any(r["case_id"] == sample["case_id"] for r in results)


def test_find_relevant_precedents_by_embedding_uses_mocked_vectors(monkeypatch):
    """埋め込みベクトルが用意されている場合はコサイン類似度検索で最良一致を返す。"""
    cases = precedent_cases.list_cases()
    assert len(cases) >= 2
    target = cases[0]
    other = cases[1]

    fake_vectors = {
        target["case_id"]: [1.0, 0.0, 0.0],
        other["case_id"]: [0.0, 1.0, 0.0],
    }
    monkeypatch.setattr(precedent_cases, "_load_embeddings", lambda: fake_vectors)
    monkeypatch.setattr(precedent_cases, "_embed_query", lambda text: [1.0, 0.0, 0.0])

    results = precedent_cases.find_relevant_precedents_by_embedding(
        ordinance_name="テスト条例",
        alleged_ground="テスト理由",
        authority="",
        top_k=1,
        min_similarity=0.5,
    )
    assert len(results) == 1
    assert results[0]["case_id"] == target["case_id"]


def test_find_relevant_precedents_by_embedding_respects_min_similarity(monkeypatch):
    """類似度が閾値未満の場合は結果を返さない（Ngramへのフォールバックもしない）。"""
    cases = precedent_cases.list_cases()
    assert cases
    sample = cases[0]
    fake_vectors = {sample["case_id"]: [1.0, 0.0]}
    monkeypatch.setattr(precedent_cases, "_load_embeddings", lambda: fake_vectors)
    monkeypatch.setattr(precedent_cases, "_embed_query", lambda text: [0.0, 1.0])

    results = precedent_cases.find_relevant_precedents_by_embedding(
        ordinance_name="無関係", alleged_ground="無関係", authority="", min_similarity=0.9,
    )
    assert results == []


def test_embed_query_returns_none_on_api_error(monkeypatch):
    """embed_content呼び出しが失敗した場合はNoneを返す（呼び出し側がフォールバックする）。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")

    class _FakeModels:
        def embed_content(self, model, contents):
            raise RuntimeError("API error")

    class _FakeClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    import types as _types
    fake_genai_module = _types.SimpleNamespace(Client=_FakeClient)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google", _types.SimpleNamespace(genai=fake_genai_module))

    assert precedent_cases._embed_query("テストクエリ") is None


def test_build_counter_argument_uses_real_precedents_when_available(monkeypatch):
    from ordinance_data import get_ordinance

    cases = precedent_cases.list_cases()
    assert cases
    sample = cases[0]

    class FakeOrdinance:
        authority = sample["authority"]
        ordinance_name = sample["basis_laws"]

    civic_agent = agent.get_agent()
    result = civic_agent.build_counter_argument(
        non_disclosure_decision="テスト用の不開示決定文",
        ordinance=FakeOrdinance(),
        alleged_ground=sample["summary"],
    )
    assert result.precedent_cases, "precedent_casesが空です"
    assert any(sample["attribution"] in text for text in result.precedent_cases)
