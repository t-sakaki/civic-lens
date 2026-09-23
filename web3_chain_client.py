"""Civic Lens — 実チェーン接続クライアント (EAS on Base Sepolia)

Ethereum Attestation Service (EAS) はOP-Stack系チェーン (Base, Optimism等) に
プリデプロイ済みのコントラクトとして固定アドレスで存在するため、独自デプロイは不要。
このモジュールは web3.py 経由でその実コントラクトに attest() トランザクションを
送信し、ローカルで捏造したtx_hashではなく実際のオンチェーン結果を返す。

必要な環境変数:
  CHAIN_RPC_URL   - RPCエンドポイント (例: https://sepolia.base.org)
  CHAIN_PRIVATE_KEY - 公証用ウォレットの秘密鍵 (本番はSecret Manager/KMS経由を推奨)
  EAS_SCHEMA_UID  - register_schema.py で事前登録したスキーマのUID (0x...)

未設定の場合は例外を送出する。ローカル生成の偽tx_hashにフォールバックしない。
"""

import os
from typing import Optional
from web3 import Web3

# OP-Stack共通プリデプロイアドレス (Base / Base Sepolia / Optimism 等で共通)
EAS_CONTRACT_ADDRESS = "0x4200000000000000000000000000000000000021"
SCHEMA_REGISTRY_ADDRESS = "0x4200000000000000000000000000000000000020"

EAS_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "bytes32", "name": "schema", "type": "bytes32"},
                    {
                        "components": [
                            {"internalType": "address", "name": "recipient", "type": "address"},
                            {"internalType": "uint64", "name": "expirationTime", "type": "uint64"},
                            {"internalType": "bool", "name": "revocable", "type": "bool"},
                            {"internalType": "bytes32", "name": "refUID", "type": "bytes32"},
                            {"internalType": "bytes", "name": "data", "type": "bytes"},
                            {"internalType": "uint256", "name": "value", "type": "uint256"},
                        ],
                        "internalType": "struct AttestationRequestData",
                        "name": "data",
                        "type": "tuple",
                    },
                ],
                "internalType": "struct AttestationRequest",
                "name": "request",
                "type": "tuple",
            }
        ],
        "name": "attest",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

EAS_GET_ATTESTATION_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "uid", "type": "bytes32"}],
        "name": "getAttestation",
        "outputs": [
            {
                "components": [
                    {"internalType": "bytes32", "name": "uid", "type": "bytes32"},
                    {"internalType": "bytes32", "name": "schema", "type": "bytes32"},
                    {"internalType": "uint64", "name": "time", "type": "uint64"},
                    {"internalType": "uint64", "name": "expirationTime", "type": "uint64"},
                    {"internalType": "uint64", "name": "revocationTime", "type": "uint64"},
                    {"internalType": "bytes32", "name": "refUID", "type": "bytes32"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "address", "name": "attester", "type": "address"},
                    {"internalType": "bool", "name": "revocable", "type": "bool"},
                    {"internalType": "bytes", "name": "data", "type": "bytes"},
                ],
                "internalType": "struct Attestation",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

SCHEMA_REGISTRY_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "schema", "type": "string"},
            {"internalType": "address", "name": "resolver", "type": "address"},
            {"internalType": "bool", "name": "revocable", "type": "bool"},
        ],
        "name": "register",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ATTESTED_EVENT_TOPIC = Web3.keccak(text="Attested(address,address,bytes32,bytes32)")
ZERO_BYTES32 = "0x" + "00" * 32


class ChainClientNotConfigured(RuntimeError):
    """RPC/秘密鍵が未設定のため実チェーン操作ができないことを示す"""


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ChainClientNotConfigured(
            f"環境変数 {name} が未設定です。実チェーンへのオンチェーン刻印には "
            f"CHAIN_RPC_URL / CHAIN_PRIVATE_KEY と、用途ごとのスキーマUID環境変数"
            f"（例: EAS_SCHEMA_UID, BADGE_EAS_SCHEMA_UID）の設定が必須です。"
            "（ローカル生成の疑似tx_hashへのフォールバックは行いません）"
        )
    return value


def get_web3() -> Web3:
    rpc_url = _require_env("CHAIN_RPC_URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ChainClientNotConfigured(f"RPCエンドポイントに接続できません: {rpc_url}")
    return w3


def get_notary_account(w3: Web3):
    private_key = _require_env("CHAIN_PRIVATE_KEY")
    account = w3.eth.account.from_key(private_key)
    return account, private_key


def submit_attestation_onchain(
    schema_uid: Optional[str],
    recipient: str,
    encoded_data: bytes,
    revocable: bool = False,
) -> dict:
    """EASコントラクトへ実際にattestトランザクションを送信し、確定結果を返す。

    呼び出し側が用途ごとのスキーマUID(環境変数から解決済みの値)を渡すこと。
    未解決(None/空)の場合は例外を送出する。

    Returns:
        {"uid": bytes32 hex, "tx_hash": hex, "block_number": int, "chain_id": int}
    """
    if not schema_uid:
        raise ChainClientNotConfigured(
            "スキーマUIDが未設定です。用途に応じた環境変数"
            "（例: EAS_SCHEMA_UID, BADGE_EAS_SCHEMA_UID）を設定してください。"
        )
    w3 = get_web3()
    account, private_key = get_notary_account(w3)

    eas = w3.eth.contract(address=Web3.to_checksum_address(EAS_CONTRACT_ADDRESS), abi=EAS_ABI)

    recipient_address = Web3.to_checksum_address(recipient) if recipient and recipient != ZERO_ADDRESS else ZERO_ADDRESS

    request = (
        schema_uid,
        (
            recipient_address,
            0,              # expirationTime: 0 = 無期限
            revocable,
            ZERO_BYTES32,   # refUID: 参照なし
            encoded_data,
            0,              # value
        ),
    )

    tx = eas.functions.attest(request).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": w3.eth.chain_id,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status != 1:
        raise RuntimeError(f"オンチェーン刻印トランザクションが失敗しました: {Web3.to_hex(tx_hash)}")

    # Attested(address indexed recipient, address indexed attester, bytes32 uid, bytes32 indexed schemaUID)
    # uid は indexed ではないため data の先頭32バイトに入る（topics[3] は schemaUID なので使わない）。
    # hexbytes 1.0以降の .hex() は 0x を付けないため、Web3.to_hex で 0x 付きに統一する。
    attestation_uid = None
    for log in receipt.logs:
        if (
            log.address.lower() == EAS_CONTRACT_ADDRESS.lower()
            and log.topics
            and log.topics[0] == ATTESTED_EVENT_TOPIC
        ):
            attestation_uid = Web3.to_hex(log.data[:32])
            break
    if attestation_uid is None:
        raise RuntimeError(f"Attestedイベントが見つかりません: {Web3.to_hex(tx_hash)}")

    return {
        "uid": attestation_uid,
        "tx_hash": Web3.to_hex(tx_hash),
        "block_number": receipt.blockNumber,
        "chain_id": w3.eth.chain_id,
        "attester": account.address,
    }


def fetch_attestation_onchain(uid: str) -> Optional[dict]:
    """EASコントラクトから attestation を直接読み出す（読み取りのみ・秘密鍵不要）。存在しなければ None。"""
    w3 = get_web3()
    eas = w3.eth.contract(address=Web3.to_checksum_address(EAS_CONTRACT_ADDRESS), abi=EAS_GET_ATTESTATION_ABI)
    a = eas.functions.getAttestation(uid).call()
    if a[0] == b"\x00" * 32:
        return None
    return {
        "uid": Web3.to_hex(a[0]),
        "schema": Web3.to_hex(a[1]),
        "time": a[2],
        "revocation_time": a[4],
        "ref_uid": Web3.to_hex(a[5]),
        "recipient": a[6],
        "attester": a[7],
        "revocable": a[8],
        "data": bytes(a[9]),
        "chain_id": w3.eth.chain_id,
    }


ERC20_TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()

USDC_ADDRESSES = {
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",   # Base Mainnet USDC
    84532: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # Base Sepolia USDC
}


def verify_erc20_transfer(
    tx_hash: str,
    expected_from: str,
    expected_to: str,
    min_amount_units: int,
) -> dict:
    """指定tx_hashが実際に expected_from -> expected_to への ERC-20 Transfer で、
    金額が min_amount_units 以上であることをオンチェーンで検証する。

    ユーザー申告の金額をそのまま信用せず、必ずこの検証を通した結果だけを記録すること。
    """
    w3 = get_web3()
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    if receipt is None or receipt.status != 1:
        raise ValueError(f"トランザクションが見つからないか失敗しています: {tx_hash}")

    chain_id = w3.eth.chain_id
    usdc_address = USDC_ADDRESSES.get(chain_id)
    expected_from_cs = Web3.to_checksum_address(expected_from)
    expected_to_cs = Web3.to_checksum_address(expected_to)

    for log in receipt.logs:
        if usdc_address and log.address.lower() != usdc_address.lower():
            continue
        if len(log.topics) != 3 or log.topics[0].hex() != ERC20_TRANSFER_TOPIC:
            continue
        from_addr = Web3.to_checksum_address("0x" + log.topics[1].hex()[-40:])
        to_addr = Web3.to_checksum_address("0x" + log.topics[2].hex()[-40:])
        amount = int.from_bytes(log.data, byteorder="big")
        if from_addr == expected_from_cs and to_addr == expected_to_cs and amount >= min_amount_units:
            return {
                "verified": True,
                "amount_units": amount,
                "block_number": receipt.blockNumber,
                "chain_id": chain_id,
                "token_address": log.address,
            }

    raise ValueError(
        f"tx {tx_hash} 内に {expected_from} -> {expected_to} への"
        f"最低{min_amount_units}単位以上のUSDC送金ログが見つかりませんでした。"
    )


def register_schema(schema_raw: str, revocable: bool = False) -> dict:
    """スキーマをSchemaRegistryに一度だけ登録する(セットアップスクリプト用)"""
    w3 = get_web3()
    account, private_key = get_notary_account(w3)
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(SCHEMA_REGISTRY_ADDRESS), abi=SCHEMA_REGISTRY_ABI
    )
    tx = registry.functions.register(schema_raw, ZERO_ADDRESS, revocable).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": w3.eth.chain_id,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    schema_uid = None
    for log in receipt.logs:
        if log.address.lower() == SCHEMA_REGISTRY_ADDRESS.lower() and len(log.topics) >= 2:
            schema_uid = Web3.to_hex(log.topics[1])
            break
    return {"schema_uid": schema_uid, "tx_hash": Web3.to_hex(tx_hash), "block_number": receipt.blockNumber}
