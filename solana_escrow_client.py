"""Civic Lens — Solanaエスクロープログラム (solana/programs/civic_lens_escrow) のPythonクライアント

ResolutionAgent が総務省DBの裁決を根拠に `propose_resolution` を送信するための最小実装。
Anchorの命令エンコード（8バイトdiscriminator + Borsh引数）を直接組み立てるため、
anchorpy等には依存せず solders のみを使う。

必要な環境変数:
  SOLANA_RPC_URL              - 例: https://api.devnet.solana.com
  SOLANA_AGENT_KEYPAIR        - エージェント鍵（solana-keygen形式のJSONファイルパス、またはJSON配列文字列）
  SOLANA_ESCROW_PROGRAM_ID    - 省略時は declare_id! の値

未設定の場合は例外を送出する。ローカル生成の偽署名にフォールバックしない（web3_chain_client.py と同方針）。
"""

import base64
import hashlib
import json
import os
import struct
import unicodedata
from enum import IntEnum

import httpx

DEFAULT_PROGRAM_ID = "Ca7tu8tfHfJt4TgEzcPxKWtYiYUCigvWh6tr78Q7TXQ4"
MAX_URI = 200


class SolanaClientNotConfigured(RuntimeError):
    pass


class Outcome(IntEnum):
    """プログラム側 `Outcome` enum と同じ並び（Borshのvariant index）"""
    GRANTED = 0            # 認容
    PARTIALLY_GRANTED = 1  # 一部認容
    DISMISSED = 2          # 棄却
    REJECTED = 3           # 却下
    WITHDRAWN = 4          # 取下げ

    @property
    def label_ja(self) -> str:
        return {0: "認容", 1: "一部認容", 2: "棄却", 3: "却下", 4: "取下げ"}[self.value]

    @property
    def releases_funds(self) -> bool:
        return self in (Outcome.GRANTED, Outcome.PARTIALLY_GRANTED)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def review_key_hash(authority: str, disposition_type: str, filed_date: str) -> bytes:
    """ケースと裁決を突合するキー: sha256(審査庁 | 処分の種類 | 申立日)。個人情報は含めない。"""
    raw = "|".join(_normalize(v) for v in (authority, disposition_type, filed_date))
    return hashlib.sha256(raw.encode("utf-8")).digest()


def ruling_text_hash(text: str) -> bytes:
    """裁決本文のハッシュ。表記揺れ（全角半角・空白）で値が変わらないよう正規化してから計算する。"""
    return hashlib.sha256(_normalize(text).encode("utf-8")).digest()


def instruction_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def encode_propose_resolution(outcome: Outcome, source_hash: bytes, source_uri: str) -> bytes:
    if len(source_hash) != 32:
        raise ValueError("source_hash must be 32 bytes")
    uri = source_uri.encode("utf-8")
    if len(uri) > MAX_URI:
        raise ValueError(f"source_uri must be <= {MAX_URI} bytes")
    return (
        instruction_discriminator("propose_resolution")
        + struct.pack("<B", int(outcome))
        + source_hash
        + struct.pack("<I", len(uri))
        + uri
    )


def _solders():
    try:
        from solders.pubkey import Pubkey  # type: ignore
        from solders.keypair import Keypair  # type: ignore
        from solders.instruction import Instruction, AccountMeta  # type: ignore
        from solders.message import Message  # type: ignore
        from solders.transaction import Transaction  # type: ignore
        from solders.hash import Hash  # type: ignore
    except ImportError as e:
        raise SolanaClientNotConfigured("solders が未インストールです（pip install solders）") from e
    return Pubkey, Keypair, Instruction, AccountMeta, Message, Transaction, Hash


def program_id():
    Pubkey = _solders()[0]
    return Pubkey.from_string(os.getenv("SOLANA_ESCROW_PROGRAM_ID", DEFAULT_PROGRAM_ID))


def find_config_pda():
    Pubkey = _solders()[0]
    return Pubkey.find_program_address([b"config"], program_id())[0]


def find_case_pda(case_id: int):
    Pubkey = _solders()[0]
    return Pubkey.find_program_address([b"case", struct.pack("<Q", case_id)], program_id())[0]


def build_propose_resolution_ix(agent_pubkey, case_id: int, outcome: Outcome, source_hash: bytes, source_uri: str):
    _, _, Instruction, AccountMeta, *_ = _solders()
    accounts = [
        AccountMeta(find_config_pda(), is_signer=False, is_writable=False),
        AccountMeta(find_case_pda(case_id), is_signer=False, is_writable=True),
        AccountMeta(agent_pubkey, is_signer=True, is_writable=False),
    ]
    return Instruction(program_id(), encode_propose_resolution(outcome, source_hash, source_uri), accounts)


def _load_agent_keypair():
    Keypair = _solders()[1]
    raw = os.getenv("SOLANA_AGENT_KEYPAIR")
    if not raw:
        raise SolanaClientNotConfigured("環境変数 SOLANA_AGENT_KEYPAIR が未設定です")
    if not raw.lstrip().startswith("["):
        with open(os.path.expanduser(raw)) as f:
            raw = f.read()
    return Keypair.from_bytes(bytes(json.loads(raw)))


def _rpc(method: str, params: list):
    url = os.getenv("SOLANA_RPC_URL")
    if not url:
        raise SolanaClientNotConfigured("環境変数 SOLANA_RPC_URL が未設定です")
    resp = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"Solana RPC error ({method}): {body['error']}")
    return body["result"]


def send_propose_resolution(case_id: int, outcome: Outcome, source_hash: bytes, source_uri: str) -> str:
    """propose_resolution を送信し、トランザクション署名を返す。"""
    _, _, _, _, Message, Transaction, Hash = _solders()
    agent = _load_agent_keypair()
    ix = build_propose_resolution_ix(agent.pubkey(), case_id, outcome, source_hash, source_uri)
    blockhash = Hash.from_string(_rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])
    tx = Transaction([agent], Message([ix], agent.pubkey()), blockhash)
    encoded = base64.b64encode(bytes(tx)).decode()
    return _rpc("sendTransaction", [encoded, {"encoding": "base64", "preflightCommitment": "confirmed"}])
