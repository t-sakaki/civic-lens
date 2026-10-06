import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import ledger_reactions as lr
import onchain_ledger as ol

UID = "0x9141b625cb51f4490d6d4f34f9ec2d69b5af2a9128edc58aab56dfc719845c6e"
AIKON = "愛知県が運営する婚活支援ポータルサイト「あいこんナビ」における個人情報誤掲載（漏洩）事件に関する以下の行政文書。"


def gql_attestation(uid=UID, revoked=False, requested=AIKON, authority="愛知県知事"):
    fields = [
        ("recordId", "aichi-aikon-navi-20260924"),
        ("authority", authority),
        ("requestedDocuments", requested),
        ("documentHash", "0x3ff5b7b322336a09a73d188589321716ad9e5f885d93564d4499f61f89f54e7b"),
        ("timestamp", {"type": "BigNumber", "hex": "0x6ab3f7aa"}),
        ("legalBasis", "愛知県情報公開条例"),
    ]
    return {
        "id": uid,
        "time": 1790179246,  # 2026-09-24 01:00:46 JST
        "txid": "0x007d90ec41872bf900cc23a0502490bf7d0dba2cb5e53b1260d40a39464a1b9c",
        "attester": "0x2a9B2cfeC60210d713dCCcE3943c8Ff0e9EE299b",
        "recipient": "0x" + "00" * 20,
        "revoked": revoked,
        "refUID": "0x" + "00" * 32,
        "decodedDataJson": json.dumps([{"name": n, "value": {"value": v}} for n, v in fields], ensure_ascii=False),
    }


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EAS_SCHEMA_UID", "0x3de8ea7980a484e5fa14166785cb0fdb7d01a9984e156bf0ee95ab894724b4ec")
    monkeypatch.setenv("LEDGER_ATTESTER_ADDRESS", "0x2a9B2cfeC60210d713dCCcE3943c8Ff0e9EE299b")
    monkeypatch.setenv("EAS_CHAIN_ID", "84532")
    # CIではFirestoreエミュレータが有効なため、JSON経路のテストであることを明示する
    # （Firestore経路は tests/test_storage_firestore.py で検証）
    monkeypatch.setenv("CIVIC_LENS_STORAGE", "json")
    monkeypatch.setattr(lr, "_STORE_PATH", tmp_path / "reactions.json")
    ol._cache.update(at=0.0, entries=None)


@pytest.fixture
def graphql(monkeypatch):
    calls = []
    attestations = [gql_attestation(), gql_attestation(uid="0x" + "ee" * 32, revoked=True)]

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"attestations": attestations}}

    def fake_post(url, json, timeout):
        calls.append((url, json["variables"]))
        return Resp()

    monkeypatch.setattr(ol.httpx, "post", fake_post)
    return calls


def test_aichi_deadline_follows_ordinance_article_12():
    # 愛知県情報公開条例第12条: 請求があった日から起算して15日以内（請求日を1日目とする）、延長30日以内
    recorded = datetime(2026, 9, 24, 1, 0, 46, tzinfo=ol.JST)
    dl = ol.compute_deadline("愛知県知事", recorded, today=datetime(2026, 9, 24, 12, 0, tzinfo=ol.JST))
    assert (dl["deadline"], dl["extended_deadline"]) == ("2026-10-08", "2026-11-07")
    assert dl["days_remaining"] == 14 and dl["phase"] == "期限内"
    later = ol.compute_deadline("愛知県知事", recorded, today=datetime(2026, 10, 20, tzinfo=ol.JST))
    assert later["phase"] == "原則期限を経過（延長期間内の可能性）"
    assert ol.compute_deadline("存在しない機関", recorded) is None


def test_fetch_reads_chain_and_skips_revoked(graphql):
    entries = ol.fetch_ledger_entries()
    assert [e["uid"] for e in entries] == [UID]
    e = entries[0]
    assert e["authority"] == "愛知県知事" and e["is_public"] and e["requested_documents"] == AIKON
    assert e["recorded_at_display"] == "2026年09月24日 01:00"
    assert e["explorer_url"].endswith(UID) and "sepolia.basescan.org/tx/0x007d" in e["tx_url"]
    url, variables = graphql[0]
    assert "base-sepolia.easscan.org/graphql" in url
    # 台帳は署名者を問わず（市民本人のウォレット署名も含む）スキーマUIDだけで絞り込む
    assert variables == {"schema": "0x3de8ea7980a484e5fa14166785cb0fdb7d01a9984e156bf0ee95ab894724b4ec"}
    assert e["signer_type"] == "official" and e["tip_recipient"] is None
    ol.fetch_ledger_entries()
    assert len(graphql) == 1, "60秒以内はキャッシュを使う"


def test_private_entry_has_no_plaintext(graphql, monkeypatch):
    monkeypatch.setattr(ol, "_cache", {"at": 0.0, "entries": None})
    graphql.clear()
    e = ol._parse(gql_attestation(requested=""), 84532, None)
    assert not e["is_public"] and e["requested_documents"] == ""


def test_reactions_toggle_and_hide_identity():
    r = lr.toggle_reaction(UID, "user-a", "watch")
    assert r["watch"]["count"] == 1 and r["watch"]["mine"]
    lr.toggle_reaction(UID, "user-b", "watch")
    assert lr.get_reactions(UID, "user-b")["watch"] == {"emoji": "👀", "label": "見守る", "count": 2, "mine": True}
    assert lr.get_reactions(UID)["watch"]["mine"] is False
    r = lr.toggle_reaction(UID, "user-a", "watch")  # 2回押すと取り消し
    assert r["watch"]["count"] == 1 and not r["watch"]["mine"]
    stored = lr._STORE_PATH.read_text(encoding="utf-8")
    assert "user-a" not in stored and "user-b" not in stored, "ユーザーIDは平文で保存しない"
    with pytest.raises(ValueError):
        lr.toggle_reaction(UID, "user-a", "angry")


@pytest.fixture
def client(graphql):
    import app as appmod

    return TestClient(appmod.app), appmod


def test_api_ledger_lists_entries_with_reactions(client):
    c, _ = client
    body = c.get("/api/ledger").json()
    assert body["logged_in"] is False and body["network"].startswith("Base Sepolia")
    assert body["entries"][0]["reactions"]["watch"]["count"] == 0


def test_api_react_requires_login_and_known_uid(client):
    c, appmod = client
    assert c.post(f"/api/ledger/{UID}/react", data={"reaction": "watch"}).status_code == 401

    user = appmod.User(user_id="u1", username="t", civic_id="c", created_at="", last_login="")
    appmod.app.dependency_overrides[appmod.get_current_user_optional] = lambda: user
    try:
        r = c.post(f"/api/ledger/{UID}/react", data={"reaction": "want_to_know"})
        assert r.status_code == 200 and r.json()["reactions"]["want_to_know"]["mine"]
        assert c.post(f"/api/ledger/{'0x' + 'ab' * 32}/react", data={"reaction": "watch"}).status_code == 404
        assert c.post(f"/api/ledger/{UID}/react", data={"reaction": "angry"}).status_code == 400
        assert c.get("/api/ledger").json()["entries"][0]["reactions"]["want_to_know"]["count"] == 1
    finally:
        appmod.app.dependency_overrides.clear()


def test_ledger_page_renders(client):
    c, _ = client
    r = c.get("/ledger")
    assert r.status_code == 200 and "開示請求台帳" in r.text


def _wallet_body(reaction="watch", uid=UID, timestamp=None, account=None):
    import time

    from eth_account import Account
    from eth_account.messages import encode_defunct

    account = account or Account.create()
    ts = int(time.time()) if timestamp is None else timestamp
    sig = Account.sign_message(encode_defunct(text=lr.build_wallet_message(uid, reaction, ts)), account.key).signature.hex()
    return {"reaction": reaction, "address": account.address, "timestamp": ts, "signature": sig}, account


def test_api_react_wallet_signature_toggles_without_login(client):
    c, _ = client
    body, account = _wallet_body("watch")
    r = c.post(f"/api/ledger/{UID}/react-wallet", json=body)
    assert r.status_code == 200 and r.json()["reactions"]["watch"]["count"] == 1
    # 自分の反応済み表示はアドレス指定時だけ。誰が反応したかは返さない
    mine = c.get("/api/ledger", params={"address": account.address}).json()["entries"][0]["reactions"]
    assert mine["watch"]["mine"] is True
    assert c.get("/api/ledger").json()["entries"][0]["reactions"]["watch"]["mine"] is False
    body2, _ = _wallet_body("watch", account=account)
    assert c.post(f"/api/ledger/{UID}/react-wallet", json=body2).json()["reactions"]["watch"]["count"] == 0


def test_api_react_wallet_rejects_bad_signatures(client):
    from eth_account import Account

    c, _ = client
    body, _ = _wallet_body("watch")
    assert c.post(f"/api/ledger/{UID}/react-wallet", json={**body, "address": Account.create().address}).status_code == 400
    assert c.post(f"/api/ledger/{UID}/react-wallet", json={**body, "reaction": "want_to_know"}).status_code == 400
    assert c.post(f"/api/ledger/{UID}/react-wallet", json={**body, "signature": "0x00"}).status_code == 400
    old, _ = _wallet_body("watch", timestamp=1)
    assert c.post(f"/api/ledger/{UID}/react-wallet", json=old).status_code == 400
    other = "0x" + "ab" * 32
    body3, _ = _wallet_body("watch", uid=other)
    assert c.post(f"/api/ledger/{other}/react-wallet", json=body3).status_code == 404
    assert c.get("/api/ledger").json()["entries"][0]["reactions"]["watch"]["count"] == 0


def test_reactions_use_upstash_when_configured(monkeypatch):
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "tok")
    sets: dict = {}

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_post(url, json, headers, timeout):
        assert url == "https://example.upstash.io/pipeline" and headers["Authorization"] == "Bearer tok"
        out = []
        for cmd, k, *rest in json:
            members = sets.setdefault(k, set())
            if cmd == "SCARD":
                out.append({"result": len(members)})
            elif cmd == "SISMEMBER":
                out.append({"result": int(rest[0] in members)})
            elif cmd == "SADD":
                members.add(rest[0]); out.append({"result": 1})
            elif cmd == "SREM":
                members.discard(rest[0]); out.append({"result": 1})
        return Resp(out)

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    r = lr.toggle_reaction(UID, "user-a", "watch")
    assert r["watch"]["count"] == 1 and r["watch"]["mine"] is True
    assert lr.get_reactions(UID)["watch"]["mine"] is False
    # 保存されるのは生のユーザーIDではなくハッシュ
    assert all("user-a" not in m for v in sets.values() for m in v)
    assert lr.toggle_reaction(UID, "user-a", "watch")["watch"]["count"] == 0
