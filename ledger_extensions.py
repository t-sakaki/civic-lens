"""Civic Lens — 決定期間の延長（延長決定）のオンチェーン記録の読み取り

開示請求台帳（onchain_ledger.py）の各記録に対し、行政機関が出した「決定期間の延長」を、
請求者本人が自分のウォレットでEASに刻印したものを読み出して台帳に並べる。
投げ銭（ledger_tips.py）と同じく、記録は常にユーザー本人のウォレットが署名・送信し、
サーバーは代理署名せず、チェーン上の事実を読み出すだけで別データベースは持たない。

延長決定の attestation スキーマ（EXTENSION_SCHEMA_RAW）:
    string  kind          - 「期間延長」または「特例延長」
    uint256 decisionDate   - 延長決定の日（UNIX秒）
    uint256 newDeadline    - 延長後の決定期限（UNIX秒。通知書に記載された日）
    string  reasonSummary  - 通知書記載の延長理由の要約（個人情報・職員名を含めない）
    bytes32 noticeHash     - 通知書全文のSHA-256ハッシュ（全文そのものは載せない）
refUID で、対象の開示請求 attestation（EAS_SCHEMA_UID）に紐づく。

オンチェーンは削除できないため、通知書の全文・請求者や職員の氏名は載せない。
公開するのは日付・機関の行為・個人情報を除いた理由の要約・ハッシュだけにとどめる。
延長理由の要約は通知書の記載の転記であり、AIによる評価は含めない。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from eth_abi import decode as abi_decode

from onchain_ledger import EAS_GRAPHQL_URLS, TX_EXPLORER_HOSTS, EAS_EXPLORER_HOSTS, LedgerNotConfigured, ledger_config

JST = timezone(timedelta(hours=9))
ZERO_BYTES32 = "0x" + "00" * 32

EXTENSION_SCHEMA_RAW = (
    "string kind, uint256 decisionDate, uint256 newDeadline, string reasonSummary, bytes32 noticeHash"
)
EXTENSION_KINDS = ("期間延長", "特例延長")
# 理由の要約の上限（UTF-8バイト数。日本語約200文字）
MAX_REASON_SUMMARY_BYTES = 600

_CACHE_TTL_SECONDS = 60
_cache: Dict[str, Any] = {"at": 0.0, "by_request": None}

_QUERY = """
query Extensions($schema: String!) {
  attestations(
    where: { schemaId: { equals: $schema } }
    orderBy: [{ time: desc }]
    take: 500
  ) { id attester time txid revoked refUID data }
}
"""


def extension_schema_uid() -> str:
    uid = os.getenv("EXTENSION_SCHEMA_UID")
    if not uid:
        raise LedgerNotConfigured(
            "環境変数 EXTENSION_SCHEMA_UID が未設定です。延長決定用スキーマを登録してから設定してください。"
        )
    return uid


def _decode(data_hex: str):
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    return abi_decode(["string", "uint256", "uint256", "string", "bytes32"], raw)


def _to_date(unix_seconds: int):
    return datetime.fromtimestamp(int(unix_seconds), JST).date()


def _parse(att: Dict[str, Any], chain_id: int, request_entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kind, decision_ts, new_deadline_ts, reason, notice_hash = _decode(att["data"])
    decision_date = _to_date(decision_ts)
    new_deadline = _to_date(new_deadline_ts)
    recorder = att.get("attester", "")

    # 記録者が請求者本人かどうか。請求がCivic Lens公証（代理署名）の場合は請求者のウォレットが
    # 分からないため判定できない（None）。第三者が他人の請求に偽の延長を紐づけても区別できるようにする。
    by_requester: Optional[bool] = None
    if request_entry and request_entry.get("signer_type") == "wallet":
        by_requester = recorder.lower() == (request_entry.get("attester") or "").lower()

    exceeds_limit = False
    unblocked_from = new_deadline + timedelta(days=1)
    dl = (request_entry or {}).get("deadline")
    if dl and kind == "期間延長":
        # 条例上の延長上限（目安）を超える日付が記録されている場合の事実の注記。違法の断定ではない。
        exceeds_limit = new_deadline > datetime.fromisoformat(dl["extended_deadline"]).date()

    return {
        "uid": att["id"],
        "request_uid": att.get("refUID"),
        "kind": kind,
        "decision_date": decision_date.isoformat(),
        "new_deadline": new_deadline.isoformat(),
        "reason_summary": reason,
        "notice_hash": "0x" + notice_hash.hex(),
        "recorder": recorder,
        "by_requester": by_requester,
        "recorded_at": datetime.fromtimestamp(int(att["time"]), JST).isoformat(),
        "exceeds_ordinance_limit": exceeds_limit,
        # 期限を過ぎても決定がない場合に、不作為についての審査請求を検討できる最短の日（目安）
        "inaction_review_from": unblocked_from.isoformat(),
        "explorer_url": f"{EAS_EXPLORER_HOSTS[chain_id]}/attestation/view/{att['id']}",
        "tx_url": f"{TX_EXPLORER_HOSTS[chain_id]}/tx/{att.get('txid')}",
    }


def fetch_extensions_by_request(
    request_entries: Dict[str, Dict[str, Any]], force: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """開示請求UID（小文字）-> 延長決定の記録（新しい順）。60秒キャッシュ。

    request_entries は台帳の記録（uid -> entry）。記録者の照合と上限の注記に使う。
    """
    if not force and _cache["by_request"] is not None and time.time() - _cache["at"] < _CACHE_TTL_SECONDS:
        return _cache["by_request"]
    cfg = ledger_config()
    resp = httpx.post(
        EAS_GRAPHQL_URLS[cfg["chain_id"]],
        json={"query": _QUERY, "variables": {"schema": extension_schema_uid()}},
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"EAS GraphQL error: {body['errors']}")
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for att in body["data"]["attestations"]:
        ref = (att.get("refUID") or "").lower()
        if att.get("revoked") or not ref or ref == ZERO_BYTES32:
            continue
        request_entry = request_entries.get(ref)
        if request_entry is None:
            continue  # 台帳にない請求への記録は表示しない
        try:
            parsed = _parse(att, cfg["chain_id"], request_entry)
        except Exception:
            continue  # スキーマに合わない記録は無視する
        grouped[ref].append(parsed)
    _cache.update(at=time.time(), by_request=dict(grouped))
    return _cache["by_request"]


def latest_extension(extensions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """請求者本人の記録のうち最新のもの。本人確認できない記録は「現在の期限」には使わない。"""
    own = [e for e in extensions if e["by_requester"]]
    return max(own, key=lambda e: e["recorded_at"]) if own else None
