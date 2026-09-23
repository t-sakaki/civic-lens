import os

import pytest
from eth_abi import decode as abi_decode

import web3_attestation as wa

AICHI_REQUEST = (
    "アジア競技大会・アジアパラ競技大会のボランティア募集要項、SNS利用・撮影等の規定、誓約書様式、"
    "および市議・県議等の議員による活動中のSNS投稿・情報発信に関する特例許可、例外扱い、"
    "関連する組織委員会および愛知県担当課における庁内決裁、協議記録"
)
FULL_TEXT = "開示請求書\n請求者 氏名: 山田太郎 住所: 名古屋市中区三の丸3丁目1-2\n請求する公文書: " + AICHI_REQUEST


NOTARY = "0x2a9B2cfeC60210d713dCCcE3943c8Ff0e9EE299b"
SCHEMA = "0x" + "11" * 32


@pytest.fixture
def chain(monkeypatch, tmp_path):
    """実チェーンの代わり: 送信されたデータを保持し、getAttestation 相当の読み出しにも応える"""
    sent = {}
    ledger = {}

    def fake_submit(**kwargs):
        sent.update(kwargs)
        uid = "0x" + format(len(ledger) + 1, "064x")
        ledger[uid] = {
            "uid": uid, "schema": kwargs["schema_uid"], "time": 1790179246, "revocation_time": 0,
            "ref_uid": "0x" + "00" * 32, "recipient": kwargs["recipient"], "attester": NOTARY,
            "revocable": False, "data": kwargs["encoded_data"], "chain_id": 84532,
        }
        return {"uid": uid, "attester": NOTARY, "chain_id": 84532, "tx_hash": "0x" + "cd" * 32, "block_number": 1}

    monkeypatch.setattr(wa, "submit_attestation_onchain", fake_submit)
    monkeypatch.setattr(wa, "fetch_attestation_onchain", lambda uid: ledger.get(uid.lower()))
    monkeypatch.setattr(wa, "ATTESTATION_STORAGE_PATH", str(tmp_path / "att.json"))
    monkeypatch.setenv("CIVIC_LENS_STORAGE", "json")
    monkeypatch.setenv("EAS_SCHEMA_UID", SCHEMA)
    monkeypatch.setenv("LEDGER_ATTESTER_ADDRESS", NOTARY)
    sent["_ledger"] = ledger
    return sent


def decode(sent):
    return abi_decode(["string", "string", "string", "bytes32", "uint256", "string"], sent["encoded_data"])


def test_index_stores_no_personal_data_and_reads_from_chain(chain):
    r = wa.issue_attestation("req-idx", "開示請求書", FULL_TEXT, "愛知県知事", "愛知県情報公開条例",
                             requested_documents=AICHI_REQUEST, publish_plaintext=True, owner_user_id="user-1")
    stored = open(wa.ATTESTATION_STORAGE_PATH, encoding="utf-8").read()
    assert "山田" not in stored and "三の丸" not in stored and AICHI_REQUEST not in stored, "索引に原本・平文を複製しない"
    got = wa.get_attestation("req-idx")
    assert got.uid == r.uid and got.authority == "愛知県知事" and got.requested_documents == AICHI_REQUEST
    assert got.title == "開示請求書" and got.tx_hash == r.tx_hash


def test_tampered_index_cannot_fake_a_record(chain):
    wa.issue_attestation("req-real", "t", FULL_TEXT, "愛知県知事")
    # 索引に、チェーン上に存在しないUIDを書き足しても表示されない
    wa._save_index(wa.AttestationIndexEntry(uid="0x" + "99" * 32, record_id="req-fake", title="偽", chain_id=84532,
                                            tx_hash="0x", block_number=0, created_at=""))
    assert wa.get_attestation("req-fake") is None
    assert [x.record_id for x in wa.list_all_attestations()] == ["req-real"]


def test_other_attester_or_schema_is_rejected(chain):
    r = wa.issue_attestation("req-x", "t", FULL_TEXT, "愛知県知事")
    chain["_ledger"][r.uid]["attester"] = "0x" + "12" * 20
    assert wa.get_attestation(r.uid) is None
    chain["_ledger"][r.uid]["attester"] = NOTARY
    chain["_ledger"][r.uid]["schema"] = "0x" + "22" * 32
    assert wa.get_attestation(r.uid) is None


def test_verification_kit_contains_original_for_requester_only(chain):
    r = wa.issue_attestation("req-kit", "t", FULL_TEXT, "愛知県知事")
    kit = wa.build_verification_kit(r, FULL_TEXT)
    assert kit["original_text"] == FULL_TEXT and kit["uid"] == r.uid
    assert wa.compute_document_hash(kit["original_text"]) == kit["document_hash"]
    assert FULL_TEXT not in open(wa.ATTESTATION_STORAGE_PATH, encoding="utf-8").read()


def test_public_request_records_requested_documents_in_plaintext(chain):
    r = wa.issue_attestation("req-1", "開示請求書", FULL_TEXT, "愛知県知事", "愛知県情報公開条例",
                             requested_documents=AICHI_REQUEST, publish_plaintext=True)
    record_id, authority, docs, doc_hash, _, legal = decode(chain)
    assert (record_id, authority, docs, legal) == ("req-1", "愛知県知事", AICHI_REQUEST, "愛知県情報公開条例")
    assert "0x" + doc_hash.hex() == wa.compute_document_hash(FULL_TEXT)
    # 氏名・住所は平文では送られない（全文はハッシュのみ）
    assert "山田".encode() not in chain["encoded_data"]
    assert r.requested_documents == AICHI_REQUEST and r.publish_plaintext
    assert wa.get_attestation(r.uid) and wa.get_attestation("req-1")


def test_private_request_records_hash_only(chain):
    r = wa.issue_attestation("req-2", "開示請求書", FULL_TEXT, "愛知県知事",
                             requested_documents=AICHI_REQUEST, publish_plaintext=False)
    assert decode(chain)[2] == ""
    assert r.requested_documents == ""


def test_personal_info_requires_acknowledgement(chain):
    docs = "山田太郎様に関する出張命令書"
    with pytest.raises(wa.PersonalInfoWarning) as exc:
        wa.issue_attestation("req-3", "t", FULL_TEXT, "愛知県知事", requested_documents=docs, publish_plaintext=True)
    assert exc.value.warnings[0]["match"] == "山田太郎様"
    assert "encoded_data" not in chain, "警告時はチェーンに送信しない"
    wa.issue_attestation("req-3", "t", FULL_TEXT, "愛知県知事", requested_documents=docs,
                         publish_plaintext=True, acknowledge_warnings=True)
    assert decode(chain)[2] == docs


@pytest.mark.parametrize("text", ["", "   "])
def test_public_requires_documents(chain, text):
    with pytest.raises(ValueError):
        wa.issue_attestation("r", "t", FULL_TEXT, "愛知県知事", requested_documents=text, publish_plaintext=True)


def test_public_rejects_too_long(chain):
    with pytest.raises(ValueError):
        wa.issue_attestation("r", "t", FULL_TEXT, "愛知県知事", requested_documents="あ" * 400, publish_plaintext=True)


def test_aichi_request_has_no_false_positive():
    assert wa.scan_personal_info(AICHI_REQUEST) == []


@pytest.mark.parametrize(
    "text,label",
    [
        ("鈴木さんの記録", "個人の氏名（敬称つき）"),
        ("連絡先 052-961-2111", "電話番号"),
        ("〒460-8501", "郵便番号"),
        ("三の丸3丁目", "番地・住所"),
        ("taro@example.com", "メールアドレス"),
        ("123456789012", "12桁の番号（個人番号の可能性）"),
    ],
)
def test_scan_detects_personal_info(text, label):
    assert label in [w["type"] for w in wa.scan_personal_info(text)]


def test_verify_detects_tampering(chain):
    r = wa.issue_attestation("req-4", "t", FULL_TEXT, "愛知県知事")
    assert wa.verify_attestation(r.uid, FULL_TEXT)["verified"]
    assert not wa.verify_attestation(r.uid, FULL_TEXT + "改")["verified"]
