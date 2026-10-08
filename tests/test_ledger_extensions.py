from datetime import date, datetime
from types import SimpleNamespace

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode
from fastapi.testclient import TestClient

import ledger_extensions as le
import onchain_ledger as ol
from tests.test_ledger import UID, gql_attestation

WALLET = "0x1111111111111111111111111111111111111111"
OTHER = "0x2222222222222222222222222222222222222222"
NOTARY = "0x2a9B2cfeC60210d713dCCcE3943c8Ff0e9EE299b"
EXT_SCHEMA = "0x" + "ab" * 32
NOTICE_HASH = "0x" + "11" * 32
REASON = "開示請求に係る行政文書の範囲が広範であるため、開示請求があった日から起算して45日以内にその全てについて開示決定等をすると事務の遂行に著しい支障が生ずるおそれがあるため"


def ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=ol.JST).timestamp())


def ext_att(attester=WALLET, kind="期限の特例", ref=UID, revoked=False, uid="0x" + "cd" * 32,
            authority="愛知県知事", first=(2026, 11, 5), final=(2026, 12, 28), request=(2026, 9, 22)):
    data = abi_encode(
        le._SCHEMA_TYPES,
        [authority, "8子支第1599号", kind, ts(*request), ts(2026, 10, 5), ts(*first) if first else 0, ts(*final),
         "愛知県情報公開条例第13条", REASON, bytes.fromhex("11" * 32)],
    )
    return {"id": uid, "attester": attester, "time": 1790700000, "txid": "0x" + "ef" * 32,
            "revoked": revoked, "refUID": ref, "data": "0x" + data.hex()}


def request_entry(signer_type="wallet", attester=WALLET):
    # 愛知県知事: 決定期限 請求日+14日、延長上限は請求日+44日（条例データ）。記録日は 2026-09-24
    e = ol._parse(gql_attestation(), 84532, None, today=datetime(2026, 10, 6, tzinfo=ol.JST))
    return {**e, "signer_type": signer_type, "attester": attester}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("EAS_SCHEMA_UID", "0x3de8ea7980a484e5fa14166785cb0fdb7d01a9984e156bf0ee95ab894724b4ec")
    monkeypatch.setenv("LEDGER_ATTESTER_ADDRESS", NOTARY)
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


def fetch(entry):
    return le.fetch_extensions_by_request({UID.lower(): entry}).get(UID.lower(), [])


def test_special_notice_is_read_with_both_deadlines_and_linked_to_request(monkeypatch):
    fake_chain(monkeypatch, [ext_att()])
    x = fetch(request_entry())[0]
    assert (x["authority"], x["notice_number"], x["kind"]) == ("愛知県知事", "8子支第1599号", "期限の特例")
    assert x["request_date"] == "2026-09-22" and x["decision_date"] == "2026-10-05"
    assert x["first_deadline"] == "2026-11-05" and x["final_deadline"] == "2026-12-28"
    assert x["legal_basis"] == "愛知県情報公開条例第13条"
    assert x["reason"] == REASON, "理由は通知書の原文をそのまま返す"
    assert x["notice_hash"] == NOTICE_HASH
    assert x["by_requester"] is True and x["authority_matches"] is True
    assert x["inaction_review_from"] == {"first": "2026-11-06", "final": "2026-12-29"}


def test_notice_request_date_differing_from_recorded_date_is_flagged(monkeypatch):
    fake_chain(monkeypatch, [ext_att()])
    assert fetch(request_entry())[0]["request_date_differs_from_recorded"] is True  # 通知書9/22 と 記録日9/24
    le._cache.update(at=0.0, by_request=None)
    fake_chain(monkeypatch, [ext_att(request=(2026, 9, 24))])
    assert fetch(request_entry())[0]["request_date_differs_from_recorded"] is False


def test_authority_mismatch_with_request_is_flagged(monkeypatch):
    fake_chain(monkeypatch, [ext_att(authority="名古屋市長")])
    assert fetch(request_entry())[0]["authority_matches"] is False


def test_requester_check_for_wallet_signed_request(monkeypatch):
    fake_chain(monkeypatch, [ext_att(attester=OTHER)])
    got = fetch(request_entry())
    assert got[0]["by_requester"] is False and le.latest_extension(got) is None


def test_requester_check_for_notarized_request(monkeypatch):
    # 代理署名の請求: 延長も公証アドレスの署名なら（請求者本人のログインを確認済みの経路）本人扱い
    fake_chain(monkeypatch, [ext_att(attester=NOTARY, uid="0x" + "a1" * 32), ext_att(attester=OTHER, uid="0x" + "a2" * 32)])
    got = {x["recorder"]: x for x in fetch(request_entry(signer_type="official", attester=NOTARY))}
    assert got[NOTARY]["by_requester"] is True and got[NOTARY]["signed_via"] == "official"
    assert got[OTHER]["by_requester"] is False, "代理署名の請求に、別のウォレットが付けた記録は第三者"


def test_notary_signed_extension_on_wallet_request_cannot_be_verified(monkeypatch):
    fake_chain(monkeypatch, [ext_att(attester=NOTARY)])
    assert fetch(request_entry())[0]["by_requester"] is False


def test_exceeding_ordinance_limit_is_noted_only_for_regular_extension(monkeypatch):
    # 愛知県: 請求日(9/22)+14日=10/6 が原則期限、延長上限は 9/22+44日=11/5
    fake_chain(monkeypatch, [
        ext_att(kind="期間の延長", first=None, final=(2026, 11, 10), uid="0x" + "b1" * 32),
        ext_att(kind="期間の延長", first=None, final=(2026, 11, 5), uid="0x" + "b2" * 32),
        ext_att(kind="期限の特例", uid="0x" + "b3" * 32),
    ])
    flags = {(x["kind"], x["final_deadline"]): x["exceeds_ordinance_limit"] for x in fetch(request_entry())}
    assert flags == {("期間の延長", "2026-11-10"): True, ("期間の延長", "2026-11-05"): False, ("期限の特例", "2026-12-28"): False}


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


def test_schema_has_authority_notice_number_and_reason():
    raw = le.EXTENSION_SCHEMA_RAW
    for name in ("authority", "noticeNumber", "requestDate", "firstDeadline", "finalDeadline", "legalBasis", "string reason,"):
        assert name in raw
    assert "reasonSummary" not in raw and len(le._SCHEMA_TYPES) == len(raw.split(","))


# --- 入力の検証 -------------------------------------------------------------

def fields(**over):
    base = dict(authority="愛知県知事", notice_number="8子支第1599号", kind="期限の特例", request_date="2026-09-22",
                decision_date="2026-10-05", first_deadline="2026-11-05", final_deadline="2026-12-28",
                legal_basis="愛知県情報公開条例第13条", reason=REASON, notice_hash=NOTICE_HASH)
    return {**base, **over}


def test_build_record_encodes_the_notice_exactly():
    built = le.build_extension_record(**fields())
    got = abi_decode(le._SCHEMA_TYPES, built["encoded_data"])
    assert got[0] == "愛知県知事" and got[1] == "8子支第1599号" and got[2] == "期限の特例" and got[8] == REASON
    assert got[3] == ts(2026, 9, 22) and got[5] == ts(2026, 11, 5) and got[6] == ts(2026, 12, 28)
    assert got[9].hex() == "11" * 32


@pytest.mark.parametrize("over,msg", [
    (dict(kind="延長"), "種別"),
    (dict(first_deadline=None), "相当部分"),
    (dict(kind="期間の延長"), "期限の特例のときだけ"),
    (dict(decision_date="2026-09-01"), "前後関係"),
    (dict(first_deadline="2027-01-01"), "間にしてください"),
    (dict(final_deadline="2026/12/28"), "YYYY-MM-DD"),
    (dict(notice_hash="0x1234"), "ハッシュ"),
    (dict(reason="あ" * 700), "理由が長すぎます"),
    (dict(authority=""), "実施機関"),
])
def test_build_record_rejects_bad_input(over, msg):
    with pytest.raises(le.ExtensionInputError, match=msg):
        le.build_extension_record(**fields(**over))


def test_regular_extension_has_no_first_deadline():
    built = le.build_extension_record(**fields(kind="期間の延長", first_deadline=None))
    assert abi_decode(le._SCHEMA_TYPES, built["encoded_data"])[5] == 0


def test_empty_reason_is_allowed_hash_only():
    built = le.build_extension_record(**fields(reason=""))
    assert abi_decode(le._SCHEMA_TYPES, built["encoded_data"])[8] == ""


# --- Civic Lens の代理署名 ---------------------------------------------------

def index_entry(signer_type="server", owner="user-1"):
    return SimpleNamespace(signer_type=signer_type, owner_user_id=owner)


@pytest.fixture
def chain_submit(monkeypatch):
    sent = {}

    def fake_submit(schema_uid, recipient, encoded_data, revocable=False, ref_uid=None):
        sent.update(schema_uid=schema_uid, encoded_data=encoded_data, revocable=revocable, ref_uid=ref_uid)
        return {"uid": "0x" + "77" * 32, "tx_hash": "0x" + "88" * 32, "attester": NOTARY, "block_number": 1, "chain_id": 84532}

    import web3_chain_client
    monkeypatch.setattr(web3_chain_client, "submit_attestation_onchain", fake_submit)
    return sent


def issue(owner="user-1", idx=None, **over):
    return le.issue_extension(
        request_uid=UID, owner_user_id=owner, request_entry=request_entry("official", NOTARY),
        index_entry=index_entry() if idx is None else idx, **fields(**over),
    )


def test_owner_can_record_via_server_signature(chain_submit):
    r = issue()
    assert chain_submit["ref_uid"] == UID and chain_submit["schema_uid"] == EXT_SCHEMA and chain_submit["revocable"] is True
    assert abi_decode(le._SCHEMA_TYPES, chain_submit["encoded_data"])[1] == "8子支第1599号"
    assert r["uid"] == "0x" + "77" * 32 and "sepolia.basescan.org/tx/" in r["tx_url"]


@pytest.mark.parametrize("owner,idx,msg", [
    ("user-2", None, "請求者本人だけ"),
    ("user-1", SimpleNamespace(signer_type="server", owner_user_id=None), "ログインしていなかった"),
    ("user-1", SimpleNamespace(signer_type="wallet", owner_user_id="user-1"), "ウォレットで署名"),
])
def test_server_signature_refused_unless_requester(chain_submit, owner, idx, msg):
    with pytest.raises(le.ExtensionForbidden, match=msg):
        issue(owner=owner, idx=idx)
    assert not chain_submit, "拒否した場合はチェーンに何も書かない"


def test_server_signature_refused_for_unindexed_request(chain_submit):
    with pytest.raises(le.ExtensionForbidden):
        le.issue_extension(request_uid=UID, owner_user_id="user-1", request_entry=request_entry("official", NOTARY),
                           index_entry=None, **fields())
    assert not chain_submit


def test_server_signature_refused_when_authority_differs_from_request(chain_submit):
    with pytest.raises(le.ExtensionInputError, match="一致しません"):
        issue(authority="名古屋市長")
    assert not chain_submit


# --- API --------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch, chain_submit):
    import app as appmod

    entry = request_entry("official", NOTARY)
    monkeypatch.setattr(appmod, "get_ledger_entry", lambda uid: entry if uid.lower() == UID.lower() else None)
    monkeypatch.setattr(appmod, "get_index_entry", lambda uid: index_entry())
    user = appmod.User(user_id="user-1", username="t", civic_id="c", created_at="", last_login="")
    c = TestClient(appmod.app)
    yield c, appmod, user
    appmod.app.dependency_overrides.clear()


def form(**over):
    f = fields(**over)
    return {k: v for k, v in f.items() if v is not None}


def login(appmod, user):
    appmod.app.dependency_overrides[appmod.get_current_user_optional] = lambda: user


def test_api_requires_login(api):
    c, _, _ = api
    assert c.post(f"/api/ledger/{UID}/extensions", data=form()).status_code == 401


def test_api_owner_records_and_others_are_forbidden(api, chain_submit):
    c, appmod, user = api
    login(appmod, user)
    r = c.post(f"/api/ledger/{UID}/extensions", data=form())
    assert r.status_code == 200 and r.json()["uid"] == "0x" + "77" * 32 and chain_submit["ref_uid"] == UID
    chain_submit.clear()
    login(appmod, appmod.User(user_id="user-2", username="x", civic_id="c", created_at="", last_login=""))
    assert c.post(f"/api/ledger/{UID}/extensions", data=form()).status_code == 403
    assert not chain_submit
    assert c.post(f"/api/ledger/0x{'ab' * 32}/extensions", data=form()).status_code == 404


def test_api_warns_about_personal_info_before_writing_to_chain(api, chain_submit):
    c, appmod, user = api
    login(appmod, user)
    pii = form(reason="担当 山田太郎様 電話 052-954-6106")
    r = c.post(f"/api/ledger/{UID}/extensions", data=pii)
    assert r.status_code == 422 and r.json()["detail"]["personal_info_warnings"] and not chain_submit
    assert c.post(f"/api/ledger/{UID}/extensions", data={**pii, "acknowledge_warnings": "true"}).status_code == 200


def test_api_rejects_bad_input_with_400(api, chain_submit):
    c, appmod, user = api
    login(appmod, user)
    assert c.post(f"/api/ledger/{UID}/extensions", data=form(first_deadline="2027-01-01")).status_code == 400
    assert not chain_submit


def test_api_ledger_survives_without_extension_schema(monkeypatch):
    import app as appmod

    monkeypatch.delenv("EXTENSION_SCHEMA_UID")
    monkeypatch.setattr(appmod, "fetch_ledger_entries", lambda: [request_entry()])
    monkeypatch.setattr(appmod, "get_reactions", lambda uid, user_id=None: {})
    body = TestClient(appmod.app).get("/api/ledger").json()
    assert body["entries"][0]["extensions"] == []
    assert TestClient(appmod.app).get("/api/ledger/extensions/config").status_code == 503
