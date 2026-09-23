"""Civic Lens — オンチェーン開示請求台帳の読み取り

EAS（Ethereum Attestation Service）に記録された開示請求を、Civic Lens 自身のデータベースではなく
チェーン（easscan の公開 GraphQL）から直接読み出して一覧化する。表示内容そのものが検証済みの
事実になり、Civic Lens 側で内容を改変・捏造する余地をなくすのが目的。

対象は「開示請求スキーマ（EAS_SCHEMA_UID）」かつ「Civic Lens 公証アドレス」が記録したものに限る。

処分期限の目安は、オンチェーンの記録日を請求日とみなし、条例データ（ordinance_data）の
決定期限（例: 愛知県情報公開条例第12条 = 請求日から起算して15日以内、延長30日以内）から計算する。
実際の請求日（申請の到達日）や補正期間とずれる可能性があるため、画面では「目安」と明示する。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from ordinance_data import AUTHORITIES, addressee_name

JST = timezone(timedelta(hours=9))

EAS_GRAPHQL_URLS = {
    84532: "https://base-sepolia.easscan.org/graphql",
    8453: "https://base.easscan.org/graphql",
}
EAS_EXPLORER_HOSTS = {
    84532: "https://base-sepolia.easscan.org",
    8453: "https://base.easscan.org",
}
TX_EXPLORER_HOSTS = {
    84532: "https://sepolia.basescan.org",
    8453: "https://basescan.org",
}
NETWORK_NAMES = {84532: "Base Sepolia（テストネット）", 8453: "Base"}

_CACHE_TTL_SECONDS = 60
_cache: Dict[str, Any] = {"at": 0.0, "entries": None}

_QUERY = """
query Ledger($schema: String!, $attester: String!) {
  attestations(
    where: { schemaId: { equals: $schema }, attester: { equals: $attester } }
    orderBy: [{ time: desc }]
    take: 200
  ) { id time txid revoked refUID decodedDataJson }
}
"""


class LedgerNotConfigured(RuntimeError):
    pass


def ledger_config() -> Dict[str, Any]:
    """読み取りに必要な設定。公証アドレスは LEDGER_ATTESTER_ADDRESS、未設定なら秘密鍵から導出する。"""
    schema_uid = os.getenv("EAS_SCHEMA_UID")
    if not schema_uid:
        raise LedgerNotConfigured("環境変数 EAS_SCHEMA_UID が未設定です。")
    attester = os.getenv("LEDGER_ATTESTER_ADDRESS")
    if not attester and os.getenv("CHAIN_PRIVATE_KEY"):
        from eth_account import Account

        attester = Account.from_key(os.environ["CHAIN_PRIVATE_KEY"]).address
    if not attester:
        raise LedgerNotConfigured("環境変数 LEDGER_ATTESTER_ADDRESS（公証アドレス）が未設定です。")
    chain_id = int(os.getenv("EAS_CHAIN_ID", "84532"))
    if chain_id not in EAS_GRAPHQL_URLS:
        raise LedgerNotConfigured(f"未対応のチェーンIDです: {chain_id}")
    return {"schema_uid": schema_uid, "attester": attester, "chain_id": chain_id}


def _find_authority(addressee: str):
    for info in AUTHORITIES.values():
        if addressee_name(info) == addressee:
            return info
    return None


def compute_deadline(authority: str, recorded_at: datetime, today: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """記録日を請求日とみなした処分期限の目安（「請求があった日から起算して N 日以内」= 請求日を1日目とする）。"""
    info = _find_authority(authority)
    if not info:
        return None
    today_date = (today or datetime.now(JST)).astimezone(JST).date()
    start = recorded_at.astimezone(JST).date()
    deadline = start + timedelta(days=info.request_deadline_days - 1)
    extended = deadline + timedelta(days=info.extension_days)
    remaining = (deadline - today_date).days
    if remaining >= 0:
        phase = "期限内"
    elif (extended - today_date).days >= 0:
        phase = "原則期限を経過（延長期間内の可能性）"
    else:
        phase = "延長期限も経過"
    return {
        "ordinance_name": info.ordinance_name,
        "request_deadline_days": info.request_deadline_days,
        "extension_days": info.extension_days,
        "deadline": deadline.isoformat(),
        "extended_deadline": extended.isoformat(),
        "days_remaining": remaining,
        "phase": phase,
    }


def _parse(att: Dict[str, Any], chain_id: int, today: Optional[datetime] = None) -> Dict[str, Any]:
    fields = {f["name"]: f["value"]["value"] for f in json.loads(att["decodedDataJson"])}
    recorded_at = datetime.fromtimestamp(int(att["time"]), JST)
    authority = fields.get("authority", "")
    requested = fields.get("requestedDocuments", "") or ""
    return {
        "uid": att["id"],
        "record_id": fields.get("recordId", ""),
        "authority": authority,
        "legal_basis": fields.get("legalBasis", ""),
        "requested_documents": requested,
        "is_public": bool(requested),
        "document_hash": fields.get("documentHash", ""),
        "recorded_at": recorded_at.isoformat(),
        "recorded_at_display": recorded_at.strftime("%Y年%m月%d日 %H:%M"),
        "revoked": bool(att.get("revoked")),
        "ref_uid": att.get("refUID"),
        "tx_hash": att.get("txid"),
        "explorer_url": f"{EAS_EXPLORER_HOSTS[chain_id]}/attestation/view/{att['id']}",
        "tx_url": f"{TX_EXPLORER_HOSTS[chain_id]}/tx/{att.get('txid')}",
        "deadline": compute_deadline(authority, recorded_at, today),
    }


def fetch_ledger_entries(force: bool = False) -> List[Dict[str, Any]]:
    """オンチェーンの開示請求記録を新しい順に返す（60秒キャッシュ）。"""
    if not force and _cache["entries"] is not None and time.time() - _cache["at"] < _CACHE_TTL_SECONDS:
        return _cache["entries"]
    cfg = ledger_config()
    resp = httpx.post(
        EAS_GRAPHQL_URLS[cfg["chain_id"]],
        json={"query": _QUERY, "variables": {"schema": cfg["schema_uid"], "attester": cfg["attester"]}},
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"EAS GraphQL error: {body['errors']}")
    entries = [
        _parse(a, cfg["chain_id"])
        for a in body["data"]["attestations"]
        if not a.get("revoked")
    ]
    _cache.update(at=time.time(), entries=entries)
    return entries


def get_ledger_entry(uid: str) -> Optional[Dict[str, Any]]:
    uid = uid.lower()
    return next((e for e in fetch_ledger_entries() if e["uid"].lower() == uid), None)


def ledger_meta() -> Dict[str, Any]:
    cfg = ledger_config()
    return {
        "attester": cfg["attester"],
        "schema_uid": cfg["schema_uid"],
        "network": NETWORK_NAMES[cfg["chain_id"]],
        "schema_url": f"{EAS_EXPLORER_HOSTS[cfg['chain_id']]}/schema/view/{cfg['schema_uid']}",
    }
