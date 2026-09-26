"""Civic Lens — 投げ銭（オンチェーン応援）の読み取り

開示請求台帳（onchain_ledger.py）の各記録は、開示請求そのものをEASで刻印したもの。
本モジュールはそれとは別の「投げ銭スキーマ」（TIP_SCHEMA_UID）のattestationを
EAS GraphQLインデクサから直接読み出し、集計する。

投げ銭そのもの（ETH/USDCの送金 + 応援attestationの記録）は常に応援者本人の
ウォレットが署名・送信する（サーバーは代理署名しない・送金も仲介しない）。
サーバー側はチェーン上の事実を集計して返すだけで、別データベースは持たない。

投げ銭attestationのスキーマ:
    address token     - トークンコントラクトアドレス（ゼロアドレス = ETH）
    address referrer   - 紹介者のアドレス（なければゼロアドレス）
    uint256 amount     - 投げ銭額（トークンの最小単位）
    string  comment    - 応援メッセージ
refUID で、応援対象の開示請求attestation（EAS_SCHEMA_UID）のUIDに紐づく。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import httpx
from eth_abi import decode as abi_decode
from web3 import Web3

from ens_resolve import resolve_ens_name
from onchain_ledger import EAS_GRAPHQL_URLS, ledger_config, LedgerNotConfigured

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ZERO_BYTES32 = "0x" + "00" * 64

# トークンコントラクトアドレス（チェックサム）-> (表示シンボル, decimals)
_NATIVE_TOKENS = {ZERO_ADDRESS: ("ETH", 18)}


def _known_tokens() -> Dict[str, tuple]:
    tokens = dict(_NATIVE_TOKENS)
    usdc = os.getenv("USDC_CONTRACT_ADDRESS")
    if usdc:
        tokens[Web3.to_checksum_address(usdc)] = ("USDC", 6)
    return tokens


def _short_addr(addr: str) -> str:
    return addr[:6] + "..." + addr[-4:]


def _token_info(token_address: str) -> tuple:
    checksummed = Web3.to_checksum_address(token_address)
    return _known_tokens().get(checksummed, (_short_addr(checksummed), 18))


def tip_schema_uid() -> str:
    schema_uid = os.getenv("TIP_SCHEMA_UID")
    if not schema_uid:
        raise LedgerNotConfigured(
            "環境変数 TIP_SCHEMA_UID が未設定です。投げ銭用スキーマを登録してから設定してください。"
        )
    return schema_uid


def _graphql_url() -> str:
    cfg = ledger_config()
    return EAS_GRAPHQL_URLS[cfg["chain_id"]]


def _decode_tip_data(data_hex: str):
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    token, referrer, amount, comment = abi_decode(
        ["address", "address", "uint256", "string"], raw
    )
    return token, referrer, amount, comment


def _decode_request_data(data_hex: str):
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    record_id, authority, requested_documents, _doc_hash, _ts, _legal_basis = abi_decode(
        ["string", "string", "string", "bytes32", "uint256", "string"], raw
    )
    return record_id, authority, requested_documents


_TIPS_QUERY = """
query TipAttestations($schemaId: String!) {
  attestations(where: { schemaId: { equals: $schemaId } }, orderBy: [{ time: desc }]) {
    attester
    refUID
    data
    time
  }
}
"""

_RECENT_TIPS_QUERY = """
query RecentTips($schemaId: String!, $since: Int!) {
  attestations(
    where: { schemaId: { equals: $schemaId }, time: { gte: $since } }
    orderBy: [{ time: desc }]
  ) { attester refUID data time }
}
"""

_REQUESTS_BY_UID_QUERY = """
query RequestsByUid($ids: [String!]!, $schemaId: String!) {
  attestations(where: { id: { in: $ids }, schemaId: { equals: $schemaId } }) { id attester data time }
}
"""


def _graphql(query: str, variables: dict) -> dict:
    resp = httpx.post(_graphql_url(), json={"query": query, "variables": variables}, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"EAS GraphQL error: {body['errors']}")
    return body["data"]


def _empty_breakdown():
    return defaultdict(lambda: {"total_amount": 0, "count": 0, "decimals": 18})


def build_tip_leaderboard() -> Dict[str, Any]:
    """投げ銭額の多い応援者・紹介者ランキングをチェーンから直接集計する（通貨別内訳つき）。"""
    data = _graphql(_TIPS_QUERY, {"schemaId": tip_schema_uid()})
    attestations = data["attestations"]

    tipper_breakdown: Dict[str, dict] = defaultdict(_empty_breakdown)
    referrer_breakdown: Dict[str, dict] = defaultdict(_empty_breakdown)
    tipper_tip_count: Dict[str, int] = defaultdict(int)
    referrer_tip_count: Dict[str, int] = defaultdict(int)

    for a in attestations:
        token, referrer, amount, _comment = _decode_tip_data(a["data"])
        symbol, decimals = _token_info(token)

        tipper = Web3.to_checksum_address(a["attester"])
        tipper_breakdown[tipper][symbol]["total_amount"] += amount
        tipper_breakdown[tipper][symbol]["count"] += 1
        tipper_breakdown[tipper][symbol]["decimals"] = decimals
        tipper_tip_count[tipper] += 1

        if referrer.lower() != ZERO_ADDRESS:
            referrer_cs = Web3.to_checksum_address(referrer)
            referrer_breakdown[referrer_cs][symbol]["total_amount"] += amount
            referrer_breakdown[referrer_cs][symbol]["count"] += 1
            referrer_breakdown[referrer_cs][symbol]["decimals"] = decimals
            referrer_tip_count[referrer_cs] += 1

    def _to_list(breakdown_by_addr, count_by_addr, count_label):
        ranked = sorted(count_by_addr.items(), key=lambda kv: kv[1], reverse=True)[:50]
        result = []
        for addr, _n in ranked:
            breakdown = [
                {
                    "currency": symbol,
                    "amount": stats["total_amount"] / (10 ** stats["decimals"]),
                    "count": stats["count"],
                }
                for symbol, stats in breakdown_by_addr[addr].items()
            ]
            result.append({
                "address": addr,
                "ens_name": resolve_ens_name(addr),
                count_label: count_by_addr[addr],
                "breakdown": breakdown,
            })
        return result

    return {
        "top_tippers": _to_list(tipper_breakdown, tipper_tip_count, "tip_count"),
        "top_referrers": _to_list(referrer_breakdown, referrer_tip_count, "referral_count"),
    }


def trending_requests(window_hours: int = 24, limit: int = 10) -> List[Dict[str, Any]]:
    """急上昇中の開示請求（直近window_hours時間で投げ銭が集まっている順）。

    TikTok的に「今、勢いがある」請求を可視化するため、全期間累計ではなく直近の
    応援件数でランキングする。
    """
    since = int(time.time()) - window_hours * 3600
    tips = _graphql(_RECENT_TIPS_QUERY, {"schemaId": tip_schema_uid(), "since": since})["attestations"]

    scores: Dict[str, dict] = defaultdict(lambda: {"tip_count": 0, "breakdown": defaultdict(float)})
    for t in tips:
        ref_uid = t["refUID"]
        if not ref_uid or ref_uid.lower() == ZERO_BYTES32:
            continue
        token, _referrer, amount, _comment = _decode_tip_data(t["data"])
        symbol, decimals = _token_info(token)
        scores[ref_uid]["tip_count"] += 1
        scores[ref_uid]["breakdown"][symbol] += amount / (10 ** decimals)

    if not scores:
        return []

    ranked_uids = sorted(scores.keys(), key=lambda uid: scores[uid]["tip_count"], reverse=True)[:limit]
    requests_data = _graphql(
        _REQUESTS_BY_UID_QUERY, {"ids": ranked_uids, "schemaId": ledger_config()["schema_uid"]}
    )["attestations"]
    requests_by_id = {r["id"]: r for r in requests_data}

    results = []
    for uid in ranked_uids:
        req = requests_by_id.get(uid)
        if not req:
            # refUIDが別スキーマ（他プロジェクト等）を指している投げ銭は開示請求台帳に
            # 表示できないため、急上昇ランキングからは黙って除外する。
            continue
        record_id, authority, requested_documents = _decode_request_data(req["data"])
        results.append({
            "attestation_uid": uid,
            "requester": Web3.to_checksum_address(req["attester"]),
            "record_id": record_id,
            "authority": authority,
            "requested_documents": requested_documents,
            "tip_count": scores[uid]["tip_count"],
            "breakdown": [
                {"currency": symbol, "amount": amount}
                for symbol, amount in scores[uid]["breakdown"].items()
            ],
        })
    return results


def tips_for_request(ref_uid: str) -> List[Dict[str, Any]]:
    """特定の開示請求（attestation UID）に寄せられた投げ銭の一覧（応援メッセージ付き、新しい順）。"""
    data = _graphql(_TIPS_QUERY, {"schemaId": tip_schema_uid()})
    ref_uid_lower = ref_uid.lower()
    results = []
    for a in data["attestations"]:
        if (a.get("refUID") or "").lower() != ref_uid_lower:
            continue
        token, referrer, amount, comment = _decode_tip_data(a["data"])
        symbol, decimals = _token_info(token)
        results.append({
            "tipper": Web3.to_checksum_address(a["attester"]),
            "referrer": Web3.to_checksum_address(referrer) if referrer.lower() != ZERO_ADDRESS else None,
            "currency": symbol,
            "amount": amount / (10 ** decimals),
            "comment": comment,
            "time": a["time"],
        })
    return results
