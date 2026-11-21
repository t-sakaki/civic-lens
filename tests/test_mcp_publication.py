import pytest
import mcp_publication as p
import web3_attestation as w

def prepare(summary="不開示理由の検証"):
    return p.prepare_publication("appeal", "審査請求書の本文", summary, "愛知県", "情報公開条例")

def test_preview_and_pii(monkeypatch):
    monkeypatch.setattr(w, "issue_attestation", lambda **kw: pytest.fail("unexpected send"))
    r = prepare()
    assert r["preview"]["request_type"] == "審査請求"
    assert "content" not in r["preview"]
    assert not prepare("test@example.com")["ready"]

def test_confirmation_and_replay(monkeypatch):
    calls = []
    class Record:
        def model_dump(self):
            return {"uid": "chain-result"}
    monkeypatch.setattr(w, "issue_attestation", lambda **kw: (calls.append(kw) or Record()))
    r = prepare()
    args = (r["confirmation_token"], r["preview_digest"])
    with pytest.raises(ValueError):
        p.publish_record(*args)
    with pytest.raises(ValueError):
        p.publish_record(args[0], "wrong", True)
    assert not calls
    p.publish_record(*args, True)
    assert calls[0]["request_type"] == "審査請求"
    assert calls[0]["publish_plaintext"] is True
    with pytest.raises(ValueError):
        p.publish_record(*args, True)
    assert len(calls) == 1

def test_expiry_and_network_change(monkeypatch):
    r = prepare()
    monkeypatch.setenv("EAS_CHAIN_ID", "999")
    with pytest.raises(ValueError):
        p.publish_record(r["confirmation_token"], r["preview_digest"], True)
    p._pending[r["confirmation_token"]]["expires"] = 0
    with pytest.raises(ValueError):
        p.publish_record(r["confirmation_token"], r["preview_digest"], True)
