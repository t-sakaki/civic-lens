import struct

import pytest

from resolution_agent import CaseKey, classify_by_rule, decide, extract_main_text
from solana_escrow_client import (
    Outcome,
    encode_propose_resolution,
    instruction_discriminator,
    review_key_hash,
    ruling_text_hash,
)

CASE_KEY = CaseKey("厚生労働大臣", "生活保護変更決定", "2026-01-15")


def ruling(main_text: str) -> str:
    return f"裁決書\n審査請求人 X\n処分庁 Y市福祉事務所長\n主 文\n{main_text}\n理 由\n第1 事案の概要\n本件は……棄却する旨の主張……"


@pytest.mark.parametrize(
    "main_text,expected",
    [
        ("本件審査請求に係る処分を取り消す。", Outcome.GRANTED),
        ("処分庁が行った部分開示決定を取り消し、全部を開示する。", Outcome.GRANTED),
        ("本件処分のうち、○○に係る部分を取り消す。その余の審査請求を棄却する。", Outcome.PARTIALLY_GRANTED),
        ("本件処分の一部を取り消す。", Outcome.PARTIALLY_GRANTED),
        ("本件審査請求を棄却する。", Outcome.DISMISSED),
        ("本件審査請求を却下する。", Outcome.REJECTED),
        ("本件審査請求を棄却する。ただし、本件処分が違法であることを宣言する。", Outcome.DISMISSED),
    ],
)
def test_classify_by_rule(main_text, expected):
    assert classify_by_rule(ruling(main_text)) == expected


def test_main_text_excludes_reasoning_section():
    # 理由欄の「棄却する旨の主張」に引きずられないこと
    assert "主張" not in extract_main_text(ruling("本件処分を取り消す。"))


def test_hashes_are_normalized():
    assert ruling_text_hash("主文　本件処分を取り消す。") == ruling_text_hash("主文 本件処分を取り消す。 ")
    assert review_key_hash("厚生労働大臣", "生活保護変更決定", "2026-01-15") == CASE_KEY.hash()
    assert review_key_hash("厚生労働大臣", "生活保護変更決定", "2026-01-16") != CASE_KEY.hash()


def test_encode_propose_resolution_layout():
    data = encode_propose_resolution(Outcome.PARTIALLY_GRANTED, b"\x11" * 32, "fufukudb:abc")
    assert data[:8] == instruction_discriminator("propose_resolution")
    assert data[8] == 1
    assert data[9:41] == b"\x11" * 32
    assert struct.unpack("<I", data[41:45])[0] == len("fufukudb:abc")
    assert data[45:] == b"fufukudb:abc"


def test_encode_rejects_long_uri():
    with pytest.raises(ValueError):
        encode_propose_resolution(Outcome.GRANTED, b"\x00" * 32, "あ" * 100)


def _llm(outcome, confidence=0.95, doc="裁決"):
    return {"document_type": doc, "outcome": outcome, "match_confidence": confidence, "reason": ""}


def test_decide_auto_when_rule_and_llm_agree():
    d = decide(ruling("本件処分を取り消す。"), CASE_KEY, "厚労省/2026-08-01/第123号", _llm(Outcome.GRANTED))
    assert d.auto_proposable and d.outcome == Outcome.GRANTED
    assert d.source_uri == "fufukudb:厚労省/2026-08-01/第123号"


@pytest.mark.parametrize(
    "llm",
    [
        _llm(Outcome.DISMISSED),                 # 判定不一致
        _llm(Outcome.GRANTED, confidence=0.5),   # 突合確信度不足
        _llm(Outcome.GRANTED, doc="答申"),       # 答申は使わない
    ],
)
def test_decide_requires_human_review(llm):
    d = decide(ruling("本件処分を取り消す。"), CASE_KEY, "ref", llm)
    assert not d.auto_proposable and d.outcome is None and d.reasons


def test_pda_and_instruction_accounts():
    pytest.importorskip("solders")
    from solders.pubkey import Pubkey
    from solana_escrow_client import build_propose_resolution_ix, find_case_pda, find_config_pda

    agent = Pubkey.new_unique()
    ix = build_propose_resolution_ix(agent, 3, Outcome.GRANTED, b"\x00" * 32, "fufukudb:x")
    assert [m.pubkey for m in ix.accounts] == [find_config_pda(), find_case_pda(3), agent]
    assert ix.accounts[1].is_writable and ix.accounts[2].is_signer
