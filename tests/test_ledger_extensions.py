from datetime import datetime

import pytest
from eth_abi import encode as abi_encode
from fastapi.testclient import TestClient

import ledger_extensions as le
import onchain_ledger as ol
from tests.test_ledger import UID, gql_attestation

WALLET = "0x1111111111111111111111111111111111111111"
OTHER = "0x2222222222222222222222222222222222222222"
EXT_SCHEMA = "0x" + "ab" * 32


def ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=ol.JST).timestamp())


def ext_att(attester=WALLET, new_deadline=(2026, 11, 7), kind="期間延長", ref=UID, revoked=False, uid="0x" + "cd" * 32):
    data = abi_encode(
        ["string", "uint256", "uint256", "string", "bytes32"],
        [kind, ts(2026, 10, 5), ts(*new_deadline), "対象文書が大量のため", bytes.fromhex("11" * 32)],
    )
    return {"id": uid, "attester": attester, "time": 1790700000, "txid": "0x" + "ef" * 32,
            "revoked": revoked, "refUID": ref, "data": "0x" + data.hex()}


def request_entry(signer_type="wallet", attester=WALLET):
    # 愛知県知事: 決定期限 2026-10-08、延長上限（目安）2026-11-07
    e = ol._parse(gql_attestation(), 84532, None, today=datetime(2026, 10, 6, tzinfo=ol.JST))
    return {**e, "signer_type": signer_type, "attester": attester}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("EAS_SCHEMA_UID", "0x3de8ea7980a484e5fa14166785cb0fdb7d01a9984e156bf0ee95ab894724b4ec")
    monkeypatch.setenv("EAS_CHAIN_ID", "84532")
    monkeypatch.setenv("EXTENSION_SCHEMA_UID", EXT_SCHEMA)
    le._cache.update(at=0.0, by_request=None)


def fake_chain(monkeypatch, attestations):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"attestations": attestations}}

    monkeypatch.setattr(le.httpx, "post", lambda url, json, timeout: Resp())


def test_extension_is_read_and_linked_to_request(monkeypatch):
    fake_chain(monkeypatch, [ext_att()])
    got = le.fetch_extensions_by_request({UID.lower(): request_entry()})[UID.lower()]
    assert len(got) == 1
    x = got[0]
    assert x["kind"] == "期間延長" and x["decision_date"] == "2026-10-05" and x["new_deadline"] == "2026-11-07"
    assert x["by_requester"] is True and not x["exceeds_ordinance_limit"]
    assert x["inaction_review_from"] == "2026-11-08"
    assert x["notice_hash"] == "0x" + "11" * 32


def test_third_party_record_is_flagged_and_not_used_as_current(monkeypatch):
    fake_chain(monkeypatch, [ext_att(attester=OTHER)])
    got = le.fetch_extensions_by_request({UID.lower(): request_entry()})[UID.lower()]
    assert got[0]["by_requester"] is False
    assert le.latest_extension(got) is None


def test_official_signed_request_cannot_verify_recorder(monkeypatch):
    fake_chain(monkeypatch, [ext_att()])
    got = le.fetch_extensions_by_request({UID.lower(): request_entry(signer_type="official")})[UID.lower()]
    assert got[0]["by_requester"] is None


def test_exceeding_ordinance_limit_is_noted_only_for_regular_extension(monkeypatch):
    fake_chain(monkeypatch, [ext_att(new_deadline=(2026, 12, 20)), ext_att(new_deadline=(2026, 12, 20), kind="特例延長", uid="0x" + "dd" * 32)])
    got = {x["kind"]: x for x in le.fetch_extensions_by_request({UID.lower(): request_entry()})[UID.lower()]}
    assert got["期間延長"]["exceeds_ordinance_limit"] is True
    assert got["特例延長"]["exceeds_ordinance_limit"] is False, "特例延長は条例の上限を超えうるので注記しない"


def test_ignores_revoked_unknown_request_and_malformed(monkeypatch):
    bad = {**ext_att(uid="0x" + "01" * 32), "data": "0x1234"}
    fake_chain(monkeypatch, [
        ext_att(revoked=True, uid="0x" + "02" * 32),
        ext_att(ref="0x" + "99" * 32, uid="0x" + "03" * 32),
        ext_att(ref="0x" + "00" * 32, uid="0x" + "04" * 32),
        bad,
    ])
    assert le.fetch_extensions_by_request({UID.lower(): request_entry()}) == {}


def test_schema_uid_required(monkeypatch):
    monkeypatch.delenv("EXTENSION_SCHEMA_UID")
    with pytest.raises(ol.LedgerNotConfigured):
        le.extension_schema_uid()


def test_api_ledger_survives_without_extension_schema(monkeypatch):
    import app as appmod

    monkeypatch.delenv("EXTENSION_SCHEMA_UID")
    monkeypatch.setattr(appmod, "fetch_ledger_entries", lambda: [request_entry()])
    monkeypatch.setattr(appmod, "get_reactions", lambda uid, user_id=None: {})
    body = TestClient(appmod.app).get("/api/ledger").json()
    assert body["entries"][0]["extensions"] == []
    assert TestClient(appmod.app).get("/api/ledger/extensions/config").status_code == 503
