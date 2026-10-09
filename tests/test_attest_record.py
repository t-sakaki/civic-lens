from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from web3_attestation import PersonalInfoWarning

REQUEST_TEXT = "愛知県知事 殿\n愛知県情報公開条例に基づき、あいこんナビの個人情報誤掲載に関する行政文書の開示を請求します。"
DOCS = "あいこんナビの個人情報誤掲載に関する委託契約書・業務報告書・点検監査記録・調査報告書"


def record(user_id="user-1", rid="5410-0000-1111-2222"):
    return SimpleNamespace(id=rid, user_id=user_id, target_authority="aichi-pref", request_text=REQUEST_TEXT)


@pytest.fixture
def api(monkeypatch):
    import app as appmod

    calls = []

    def fake_issue(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model_dump=lambda: {"uid": "0x" + "55" * 32, "explorer_url": "https://example/attestation"})

    monkeypatch.setattr(appmod, "get_record_by_id", lambda rid: record() if rid == "5410-0000-1111-2222" else None)
    monkeypatch.setattr(appmod, "get_records_by_user", lambda uid: [record()] if uid == "user-1" else [])
    monkeypatch.setattr(appmod, "get_index_entry", lambda rid: None)
    monkeypatch.setattr(appmod, "issue_attestation", fake_issue)
    monkeypatch.setattr(appmod, "build_verification_kit", lambda att, content: {"original_text": content})
    c = TestClient(appmod.app)
    yield c, appmod, calls
    appmod.app.dependency_overrides.clear()


def login(appmod, user_id="user-1"):
    user = appmod.User(user_id=user_id, username="t", civic_id="c", created_at="", last_login="")
    appmod.app.dependency_overrides[appmod.get_current_user_optional] = lambda: user


URL = "/api/visibility/5410-0000-1111-2222/attest"


def test_my_record_ids_only_for_the_logged_in_owner(api):
    c, appmod, _ = api
    assert c.get("/api/visibility/my-record-ids").json() == {"ids": []}
    login(appmod)
    assert c.get("/api/visibility/my-record-ids").json() == {"ids": ["5410-0000-1111-2222"]}
    login(appmod, "someone-else")
    assert c.get("/api/visibility/my-record-ids").json() == {"ids": []}


def test_requires_login(api):
    c, _, calls = api
    assert c.post(URL).status_code == 401 and not calls


def test_unknown_record_and_non_owner(api):
    c, appmod, calls = api
    login(appmod)
    assert c.post("/api/visibility/nope/attest").status_code == 404
    login(appmod, "someone-else")
    assert c.post(URL).status_code == 403 and not calls, "請求を保存した本人以外は記録できない"


def test_owner_records_with_addressee_and_ordinance(api):
    c, appmod, calls = api
    login(appmod)
    r = c.post(URL, data={"publish_plaintext": "true", "requested_documents": DOCS})
    assert r.status_code == 200 and r.json()["uid"] == "0x" + "55" * 32
    assert r.json()["verification_kit"]["original_text"] == REQUEST_TEXT
    kw = calls[0]
    assert kw["authority"] == "愛知県知事", "台帳の実施機関は名宛人（県ではなく知事）で記録する"
    assert kw["legal_basis"] == "愛知県情報公開条例" and kw["content"] == REQUEST_TEXT
    assert kw["record_id"] == "5410-0000-1111-2222" and kw["owner_user_id"] == "user-1"
    assert kw["publish_plaintext"] is True and kw["requested_documents"] == DOCS and kw["acknowledge_warnings"] is False


def test_private_request_records_hash_only(api):
    c, appmod, calls = api
    login(appmod)
    assert c.post(URL).status_code == 200
    assert calls[0]["publish_plaintext"] is False and calls[0]["requested_documents"] == ""


def test_already_recorded_is_conflict_and_not_issued_twice(api, monkeypatch):
    c, appmod, calls = api
    login(appmod)
    monkeypatch.setattr(appmod, "get_index_entry", lambda rid: SimpleNamespace(uid="0x" + "66" * 32))
    r = c.post(URL)
    assert r.status_code == 409 and r.json()["detail"]["uid"] == "0x" + "66" * 32 and not calls


def test_personal_info_warning_then_acknowledged(api, monkeypatch):
    c, appmod, calls = api
    login(appmod)

    def issue(**kwargs):
        if not kwargs["acknowledge_warnings"]:
            raise PersonalInfoWarning([{"type": "電話番号", "match": "052-954-6106"}])
        calls.append(kwargs)
        return SimpleNamespace(model_dump=lambda: {"uid": "0x" + "55" * 32})

    monkeypatch.setattr(appmod, "issue_attestation", issue)
    data = {"publish_plaintext": "true", "requested_documents": "連絡先 052-954-6106"}
    r = c.post(URL, data=data)
    assert r.status_code == 422 and r.json()["detail"]["personal_info_warnings"] and not calls
    assert c.post(URL, data={**data, "acknowledge_warnings": "true"}).status_code == 200


def test_publish_without_documents_is_a_bad_request(api, monkeypatch):
    c, appmod, _ = api
    login(appmod)

    def issue(**kwargs):
        raise ValueError("公開する場合は、請求する公文書の特定内容を入力してください。")

    monkeypatch.setattr(appmod, "issue_attestation", issue)
    assert c.post(URL, data={"publish_plaintext": "true"}).status_code == 400


def test_disclosure_request_returns_the_addressee(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "get_agent", lambda: SimpleNamespace(
        generate_disclosure_request=lambda user_input, ordinance, strategy_option="": (REQUEST_TEXT, True)))
    r = TestClient(appmod.app).post("/api/disclosure-request", data={
        "user_input": "あいこんナビについて知りたい", "target_authority": "aichi-pref"})
    assert r.status_code == 200
    body = r.json()
    assert body["authority"] == "愛知県" and body["addressee"] == "愛知県知事"


def test_index_page_has_the_feed_attest_button(api):
    c, _, _ = api
    html = c.get("/").text
    assert "attest-feed-btn" in html and "/attest" in html and "currentAddressee" in html
