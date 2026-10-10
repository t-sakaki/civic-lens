"""Civic Lens — 決定期間の延長・期限の特例（行政機関の応答）のオンチェーン記録

開示請求台帳（onchain_ledger.py）の各記録に対し、行政機関が出した「決定期間の延長」または
「期限の特例」の通知を、EASに刻印して台帳に並べる。記録の経路は2つ:

  1. 請求者本人のウォレットが直接署名（サーバーは代理署名しない）
  2. Civic Lens の代理署名 — 請求が Civic Lens の代理署名で記録されたもので、かつログイン中の
     ユーザーが請求の発行者本人（索引の owner_user_id）の場合に限る（issue_extension）

延長決定の attestation スキーマ（EXTENSION_SCHEMA_RAW）:
    string  authority      - 実施機関（例: 愛知県知事）
    string  noticeNumber   - 通知書の文書番号（例: 8子支第1599号）。なければ空
    string  kind           - 「期間の延長」または「期限の特例」
    uint256 requestDate    - 通知書に記載された開示請求の日（UNIX秒）。起算日の確認に使う
    uint256 decisionDate   - 通知の日（UNIX秒）
    uint256 firstDeadline  - 期限の特例で「相当部分」を決定する期限（UNIX秒）。期間の延長では 0
    uint256 finalDeadline  - 延長後の決定期限、または特例で「残り」を決定する期限（UNIX秒）
    string  legalBasis     - 根拠条例・条項（例: 愛知県情報公開条例第13条）
    string  reason         - 通知書に記載された理由（原文のまま。要約・言い換え・追記をしない）
    bytes32 noticeHash     - 通知書全文のSHA-256ハッシュ（全文そのものは載せない）
refUID で、対象の開示請求 attestation（EAS_SCHEMA_UID）に紐づく。

オンチェーンは削除できないため、通知書の全文・請求者や職員の氏名・連絡先は載せない。
公開するのは日付・機関の行為（通知書記載の理由の原文）・文書番号・ハッシュだけにとどめる。
理由は正確さのため通知書の記載をそのまま転記し、要約・言い換え・AIによる評価を含めない。
長すぎる、または個人情報を含む場合は、一部を省略せず理由欄を空にして通知書ハッシュだけを記録する。
"""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from eth_abi import decode as abi_decode, encode as abi_encode

from onchain_ledger import (
    EAS_GRAPHQL_URLS, TX_EXPLORER_HOSTS, EAS_EXPLORER_HOSTS, LedgerNotConfigured, ledger_config, _find_authority,
)

JST = timezone(timedelta(hours=9))
ZERO_BYTES32 = "0x" + "00" * 32

EXTENSION_SCHEMA_RAW = (
    "string authority, string noticeNumber, string kind, uint256 requestDate, uint256 decisionDate, "
    "uint256 firstDeadline, uint256 finalDeadline, string legalBasis, string reason, bytes32 noticeHash"
)
_SCHEMA_TYPES = [
    "string", "string", "string", "uint256", "uint256", "uint256", "uint256", "string", "string", "bytes32",
]
KIND_EXTENSION = "期間の延長"
KIND_SPECIAL = "期限の特例"
EXTENSION_KINDS = (KIND_EXTENSION, KIND_SPECIAL)
# 理由（通知書の原文）の上限（UTF-8バイト数。日本語約660文字）。超える場合は省略せず空欄にする
MAX_REASON_BYTES = 2000
MAX_SHORT_FIELD_BYTES = 200  # 実施機関・文書番号・根拠条例

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
    return abi_decode(_SCHEMA_TYPES, raw)


def _to_date(unix_seconds: int) -> Optional[date]:
    return datetime.fromtimestamp(int(unix_seconds), JST).date() if int(unix_seconds) else None


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _jst_midnight(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=JST).timestamp())


def _parse(att: Dict[str, Any], chain_id: int, request_entry: Optional[Dict[str, Any]], official_attester: Optional[str] = None) -> Dict[str, Any]:
    (authority, notice_number, kind, request_ts, decision_ts, first_ts, final_ts,
     legal_basis, reason, notice_hash) = _decode(att["data"])
    request_date, decision_date = _to_date(request_ts), _to_date(decision_ts)
    first_deadline, final_deadline = _to_date(first_ts), _to_date(final_ts)
    recorder = att.get("attester", "")
    req = request_entry or {}

    # 記録者が請求者本人か:
    #  - 請求が本人のウォレット署名: 記録者が同じウォレットなら本人
    #  - 請求が Civic Lens 代理署名: 延長も同じ公証アドレスの署名なら、代理署名の経路（請求の発行者本人の
    #    ログインを確認済み）で記録されたもの。それ以外のウォレットによる記録は第三者
    by_requester: Optional[bool] = None
    signed_via = "official" if official_attester and recorder.lower() == official_attester.lower() else "wallet"
    if req.get("signer_type") == "wallet":
        by_requester = recorder.lower() == (req.get("attester") or "").lower()
    elif req.get("signer_type") == "official":
        by_requester = True if signed_via == "official" else False

    # 事実の照合（違法・不当の断定ではない）
    authority_matches = bool(req) and authority == req.get("authority")
    info = _find_authority(req.get("authority", "")) if req else None
    exceeds_limit = False
    if info and request_date and final_deadline and kind == KIND_EXTENSION:
        # 通知書記載の請求日を起算日（1日目）とした、条例上の延長上限の目安
        limit = request_date + timedelta(days=info.request_deadline_days + info.extension_days - 1)
        exceeds_limit = final_deadline > limit

    return {
        "uid": att["id"],
        "request_uid": att.get("refUID"),
        "authority": authority,
        "notice_number": notice_number,
        "kind": kind,
        "request_date": _iso(request_date),
        "decision_date": _iso(decision_date),
        "first_deadline": _iso(first_deadline),
        "final_deadline": _iso(final_deadline),
        "legal_basis": legal_basis,
        "reason": reason,
        "notice_hash": "0x" + notice_hash.hex(),
        "recorder": recorder,
        "by_requester": by_requester,
        "signed_via": signed_via,
        "authority_matches": authority_matches,
        "exceeds_ordinance_limit": exceeds_limit,
        "recorded_at": datetime.fromtimestamp(int(att["time"]), JST).isoformat(),
        # 通知書記載の請求日と、台帳に記録された日（記録日）が異なる場合の事実の注記
        "request_date_differs_from_recorded": bool(
            request_date and (req.get("requested_date") or req.get("recorded_at"))
            and request_date != (
                date.fromisoformat(req["requested_date"]) if req.get("requested_date")
                else datetime.fromisoformat(req["recorded_at"]).astimezone(JST).date())
        ),
        # 期限を過ぎても決定がない場合に、不作為についての審査請求を検討できる最短の日（目安）。
        # 期限の特例では「相当部分」と「残り」で別。法的な見解が分かれうるため断定しない
        "inaction_review_from": {
            "first": _iso(first_deadline + timedelta(days=1)) if first_deadline else None,
            "final": _iso(final_deadline + timedelta(days=1)) if final_deadline else None,
        },
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
            parsed = _parse(att, cfg["chain_id"], request_entry, cfg["attester"])
        except Exception:
            continue  # スキーマに合わない記録は無視する
        grouped[ref].append(parsed)
    _cache.update(at=time.time(), by_request=dict(grouped))
    return _cache["by_request"]


def latest_extension(extensions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """請求者本人の記録のうち最新のもの。本人確認できない記録は「現在の期限」には使わない。"""
    own = [e for e in extensions if e["by_requester"]]
    return max(own, key=lambda e: e["recorded_at"]) if own else None


# ---------------------------------------------------------------------------
# Civic Lens の代理署名による記録
# ---------------------------------------------------------------------------
class ExtensionInputError(ValueError):
    """入力が不正（日付の前後関係・種別・長さ・請求との不一致など）"""


class ExtensionForbidden(PermissionError):
    """代理署名できる請求者本人ではない"""


_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _parse_date(value: Optional[str], label: str, required: bool) -> Optional[date]:
    if not value:
        if required:
            raise ExtensionInputError(f"{label}を入力してください。")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ExtensionInputError(f"{label}は YYYY-MM-DD 形式で入力してください。")


def build_extension_record(
    *, authority: str, notice_number: str, kind: str, request_date: str, decision_date: str,
    first_deadline: Optional[str], final_deadline: str, legal_basis: str, reason: str, notice_hash: str,
) -> Dict[str, Any]:
    """入力を検証し、チェーンに書き込む形（エンコード済みデータ）にする。"""
    if kind not in EXTENSION_KINDS:
        raise ExtensionInputError(f"種別は {' / '.join(EXTENSION_KINDS)} のいずれかを指定してください。")
    for label, value in (("実施機関", authority), ("文書番号", notice_number), ("根拠条例", legal_basis)):
        if len(value.encode("utf-8")) > MAX_SHORT_FIELD_BYTES:
            raise ExtensionInputError(f"{label}が長すぎます。")
    if not authority:
        raise ExtensionInputError("実施機関を入力してください。")
    if len(reason.encode("utf-8")) > MAX_REASON_BYTES:
        raise ExtensionInputError(
            "理由が長すぎます。一部を省くと原文と異なってしまうため、理由欄は空にして通知書ハッシュだけを記録してください。"
        )
    if not _HASH_RE.match(notice_hash or ""):
        raise ExtensionInputError("通知書のハッシュは 0x から始まる64桁の16進数で指定してください。")

    req = _parse_date(request_date, "開示請求の日", True)
    dec = _parse_date(decision_date, "通知の日", True)
    first = _parse_date(first_deadline, "相当部分の決定期限", kind == KIND_SPECIAL)
    final = _parse_date(final_deadline, "決定期限", True)
    if kind == KIND_EXTENSION and first:
        raise ExtensionInputError("相当部分の決定期限は、期限の特例のときだけ指定します。")
    if not (req <= dec <= final):
        raise ExtensionInputError("日付の前後関係が不正です（開示請求の日 ≦ 通知の日 ≦ 決定期限）。")
    if first and not (dec <= first <= final):
        raise ExtensionInputError("相当部分の決定期限は、通知の日から決定期限の間にしてください。")

    encoded = abi_encode(
        _SCHEMA_TYPES,
        [authority, notice_number, kind, _jst_midnight(req), _jst_midnight(dec),
         _jst_midnight(first) if first else 0, _jst_midnight(final), legal_basis, reason,
         bytes.fromhex(notice_hash[2:])],
    )
    return {"encoded_data": encoded, "request_date": req, "final_deadline": final}


def issue_extension(
    *, request_uid: str, owner_user_id: str, request_entry: Dict[str, Any], index_entry: Any, **fields: Any
) -> Dict[str, Any]:
    """Civic Lens の代理署名で延長決定を記録する。

    条件: 請求が代理署名で記録されたもので、かつ owner_user_id（ログイン中のユーザー）が
    請求の発行者本人であること。実施機関は請求の記録（オンチェーン）と一致していなければならない。
    """
    from web3_chain_client import submit_attestation_onchain

    if index_entry is None or index_entry.signer_type != "server":
        raise ExtensionForbidden("この請求は Civic Lens の代理署名で記録されたものではありません。ウォレットで署名してください。")
    if not index_entry.owner_user_id:
        raise ExtensionForbidden("この請求は発行時にログインしていなかったため、請求者本人の確認ができません。")
    if index_entry.owner_user_id != owner_user_id:
        raise ExtensionForbidden("代理署名で記録できるのは、この請求を発行した請求者本人だけです。")
    if fields["authority"] != request_entry["authority"]:
        raise ExtensionInputError(
            f"実施機関が請求の記録（{request_entry['authority']}）と一致しません。通知書の宛先を確認してください。"
        )

    built = build_extension_record(**fields)
    result = submit_attestation_onchain(
        schema_uid=extension_schema_uid(),
        recipient="0x" + "00" * 20,
        encoded_data=built["encoded_data"],
        revocable=True,
        ref_uid=request_uid,
    )
    _cache.update(at=0.0, by_request=None)  # 次の読み出しで反映する
    cfg = ledger_config()
    return {
        "uid": result["uid"],
        "tx_hash": result["tx_hash"],
        "attester": result["attester"],
        "explorer_url": f"{EAS_EXPLORER_HOSTS[cfg['chain_id']]}/attestation/view/{result['uid']}",
        "tx_url": f"{TX_EXPLORER_HOSTS[cfg['chain_id']]}/tx/{result['tx_hash']}",
    }
