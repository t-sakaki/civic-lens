"""SBTの再識別対策（ランダム市民ID・公開APIからのウォレット非表示）のテスト"""
import web3_sbt
from web3_sbt import (
    LEGACY_DEFAULT_RECIPIENT_ID, SBTRecord, get_sbt_metadata, get_user_passport,
    mint_sbt, passport_public_view,
)

WALLET = "0x1111111111111111111111111111111111111111"


def _stub_chain(monkeypatch, tmp_path):
    monkeypatch.setattr(web3_sbt, "SBT_STORAGE_PATH", str(tmp_path / "sbt.json"))
    monkeypatch.setenv("BADGE_EAS_SCHEMA_UID", "0x" + "ab" * 32)
    captured = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return {"uid": "0x" + "cd" * 32, "tx_hash": "0x" + "ef" * 32, "chain_id": 84532}

    monkeypatch.setattr(web3_sbt, "submit_attestation_onchain", fake_submit)
    return captured


def test_blank_or_legacy_default_id_becomes_random(monkeypatch, tmp_path):
    _stub_chain(monkeypatch, tmp_path)
    ids = {
        mint_sbt(rid, wallet_address=WALLET).recipient_id
        for rid in ("", "  ", LEGACY_DEFAULT_RECIPIENT_ID, LEGACY_DEFAULT_RECIPIENT_ID)
    }
    assert len(ids) == 4
    assert all(i.startswith("citizen-") and i != LEGACY_DEFAULT_RECIPIENT_ID for i in ids)


def test_client_supplied_id_is_kept(monkeypatch, tmp_path):
    _stub_chain(monkeypatch, tmp_path)
    assert mint_sbt("市民#48213", wallet_address=WALLET).recipient_id == "市民#48213"


def test_metadata_wallet_default_and_hidden(monkeypatch, tmp_path):
    _stub_chain(monkeypatch, tmp_path)
    rec = mint_sbt("", wallet_address=WALLET)
    monkeypatch.delenv("PRIVACY_HIDE_WALLET_META", raising=False)
    traits = {a["trait_type"] for a in get_sbt_metadata(rec.token_id)["attributes"]}
    assert "Wallet" in traits
    monkeypatch.setenv("PRIVACY_HIDE_WALLET_META", "1")
    traits = {a["trait_type"] for a in get_sbt_metadata(rec.token_id)["attributes"]}
    assert "Wallet" not in traits and "Recipient" in traits


def test_passport_hides_wallet_unless_queried_by_wallet(monkeypatch, tmp_path):
    _stub_chain(monkeypatch, tmp_path)
    rec = mint_sbt("", wallet_address=WALLET)
    monkeypatch.delenv("PRIVACY_HIDE_WALLET_META", raising=False)
    assert passport_public_view(rec, rec.recipient_id)["wallet_address"] == WALLET
    monkeypatch.setenv("PRIVACY_HIDE_WALLET_META", "1")
    assert "wallet_address" not in passport_public_view(rec, rec.recipient_id)
    assert passport_public_view(rec, WALLET.upper().replace("0X", "0x"))["wallet_address"] == WALLET
    assert [r.token_id for r in get_user_passport(rec.recipient_id)] == [rec.token_id]
