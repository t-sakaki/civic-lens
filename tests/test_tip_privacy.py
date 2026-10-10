"""投げ銭の再識別対策（ENS名の非表示・応援メッセージの個人情報マスク）のテスト"""
import ledger_tips
from ledger_tips import MASK, mask_personal_info


def test_mask_hides_personal_info_but_keeps_plain_comment():
    assert mask_personal_info("応援しています！頑張ってください") == "応援しています！頑張ってください"
    out = mask_personal_info("連絡は taro@example.com か 03-1234-5678 まで")
    assert "taro@example.com" not in out and "03-1234-5678" not in out and MASK in out
    assert mask_personal_info("") == "" and mask_personal_info(None) == ""


def test_ens_name_default_and_hidden(monkeypatch):
    monkeypatch.setattr(ledger_tips, "resolve_ens_name", lambda a: "peikun.eth")
    monkeypatch.delenv("PRIVACY_HIDE_ENS", raising=False)
    assert ledger_tips._display_ens_name("0xabc") == "peikun.eth"
    monkeypatch.setenv("PRIVACY_HIDE_ENS", "1")
    assert ledger_tips._display_ens_name("0xabc") is None


def test_tips_for_request_masks_comment(monkeypatch):
    from eth_abi import encode
    zero = "0x" + "00" * 20
    data = "0x" + encode(["address", "address", "uint256", "string"], [zero, zero, 10**18, "mail a@b.co"]).hex()
    monkeypatch.setattr(ledger_tips, "tip_schema_uid", lambda: "0x1")
    monkeypatch.setattr(ledger_tips, "_graphql", lambda q, v: {"attestations": [
        {"refUID": "0xAA", "attester": "0x" + "11" * 20, "data": data, "time": 1}]})
    tips = ledger_tips.tips_for_request("0xaa")
    assert tips[0]["comment"] == "mail " + MASK
