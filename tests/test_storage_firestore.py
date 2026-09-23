"""Firestore エミュレータでの結合テスト（FIRESTORE_EMULATOR_HOST 未設定ならスキップ）

    gcloud emulators firestore start --host-port=127.0.0.1:8085
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 GOOGLE_CLOUD_PROJECT=civic-lens-test pytest tests/test_storage_firestore.py
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="Firestore エミュレータが必要")

import ledger_reactions as lr  # noqa: E402
import web3_attestation as wa  # noqa: E402

NOTARY = "0x2a9B2cfeC60210d713dCCcE3943c8Ff0e9EE299b"


@pytest.fixture(autouse=True)
def firestore_mode(monkeypatch):
    monkeypatch.setenv("CIVIC_LENS_STORAGE", "firestore")


def test_reactions_persist_in_firestore():
    uid = "0x" + uuid.uuid4().hex * 2
    assert lr.toggle_reaction(uid, "u1", "watch")["watch"] == {"emoji": "👀", "label": "見守る", "count": 1, "mine": True}
    lr.toggle_reaction(uid, "u2", "watch")
    lr.toggle_reaction(uid, "u1", "fork_local")
    r = lr.get_reactions(uid, "u2")
    assert (r["watch"]["count"], r["watch"]["mine"], r["fork_local"]["count"]) == (2, True, 1)
    assert lr.toggle_reaction(uid, "u1", "watch")["watch"]["count"] == 1  # 取り消し
    raw = lr._doc(uid).get().to_dict()
    assert "u1" not in str(raw) and "u2" not in str(raw), "ユーザーIDは平文で保存しない"


def test_attestation_index_persists_in_firestore(monkeypatch):
    ledger = {}
    content = "申請番号 0269-XXXX 氏名 山田太郎\n請求文書: 委託契約書"

    def fake_submit(**kw):
        uid = "0x" + uuid.uuid4().hex * 2
        ledger[uid] = {"uid": uid, "schema": kw["schema_uid"], "time": 1790179246, "revocation_time": 0,
                       "ref_uid": "0x" + "00" * 32, "recipient": kw["recipient"], "attester": NOTARY,
                       "revocable": False, "data": kw["encoded_data"], "chain_id": 84532}
        return {"uid": uid, "attester": NOTARY, "chain_id": 84532, "tx_hash": "0x" + "cd" * 32, "block_number": 7}

    monkeypatch.setattr(wa, "submit_attestation_onchain", fake_submit)
    monkeypatch.setattr(wa, "fetch_attestation_onchain", lambda uid: ledger.get(uid.lower()))
    monkeypatch.setenv("EAS_SCHEMA_UID", "0x" + "11" * 32)
    monkeypatch.setenv("LEDGER_ATTESTER_ADDRESS", NOTARY)

    rid = f"req-{uuid.uuid4().hex[:8]}"
    r = wa.issue_attestation(rid, "委託契約の開示請求", content, "愛知県知事", owner_user_id="user-9")
    from firebase_client import get_firestore_client

    stored = get_firestore_client().collection(wa.INDEX_COLLECTION).document(r.uid.lower()).get().to_dict()
    assert stored["record_id"] == rid and stored["owner_user_id"] == "user-9"
    assert "山田" not in str(stored) and "0269" not in str(stored), "索引に個人情報を保存しない"
    assert wa.get_attestation(rid).uid == r.uid
    assert wa.verify_attestation(rid, content)["verified"]
    assert rid in [x.record_id for x in wa.list_all_attestations()]
