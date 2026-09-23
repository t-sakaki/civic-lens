"""Civic Lens — Web3 EAS (Ethereum Attestation Service) オンチェーン存在証明モジュール

開示請求書および提出事実に対して、EAS (Ethereum Attestation Service) に準拠した
改ざん不能なタイムスタンプ（存在証明）をオンチェーンに発行し、
行政機関による「請求の未受領・日付改ざん・隠蔽」を暗号学的に検証可能にする。

注意: ブロックチェーンのタイムスタンプは民法施行法上の「確定日付」ではない。
「ある時点にこの内容の文書が存在した」ことの技術的な証明である。

公開の請求では「請求する公文書の特定内容」を平文でオンチェーンに記録する
（台帳として誰でも参照・検証できるようにするため）。オンチェーンの記録は削除できないため、
記録前に個人情報らしき記述を scan_personal_info() で検出し、本人の確認を必須とする。
氏名・住所等を含む請求書全文は、常にハッシュのみを記録する。
"""

import os
import re
import json
import hashlib
import time
from typing import Optional, Dict, List
from datetime import datetime, timezone
from pydantic import BaseModel
from eth_abi import encode as abi_encode, decode as abi_decode

from web3_chain_client import (
    submit_attestation_onchain, fetch_attestation_onchain, ChainClientNotConfigured, ZERO_ADDRESS,
)
from storage_backend import use_firestore

DEFAULT_STORAGE_DIR = os.path.join(os.path.dirname(__file__), "data")
if os.getenv("VERCEL"):
    STORAGE_DIR = "/tmp/civic_lens_data"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    ATTESTATION_STORAGE_PATH = os.path.join(STORAGE_DIR, "attestations.json")
else:
    ATTESTATION_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "attestations.json")

# EAS スキーマ定義: 請求書ID, 実施機関, 請求する公文書の特定内容（公開時のみ平文）, 文書ハッシュ, 請求日時, 根拠条例
# 実際のスキーマUIDは scripts/register_eas_schema.py で SchemaRegistry に一度だけ登録し、
# 環境変数 EAS_SCHEMA_UID に設定する。ここでのローカルsha256はスキーマ内容の記録用参考値であり
# 実チェーン上のUIDとしては使用しない。
EAS_SCHEMA_RAW = (
    "string recordId, string authority, string requestedDocuments, "
    "bytes32 documentHash, uint256 timestamp, string legalBasis"
)

# 平文で記録する「請求する公文書の特定内容」の上限（UTF-8バイト数。日本語約330文字）
MAX_REQUESTED_DOCUMENTS_BYTES = 1000

# オンチェーン公開前に警告する個人情報らしき記述のパターン（誤検知は本人確認で解消する前提）
_PERSONAL_INFO_PATTERNS = [
    ("メールアドレス", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("電話番号", re.compile(r"0\d{1,4}[-(（]?\d{1,4}[-)）]?\d{3,4}")),
    ("郵便番号", re.compile(r"〒?\s?\d{3}-\d{4}")),
    ("番地・住所", re.compile(r"\d+\s?(丁目|番地|番\d+号)|\d+-\d+-\d+")),
    # 「様式」「様態」「氏名」などの一般語は除外する
    ("個人の氏名（敬称つき）", re.compile(r"[一-龥々]{2,5}\s?(氏(?!名)|様(?![式態相子])|さん|君|殿)")),
    ("12桁の番号（個人番号の可能性）", re.compile(r"(?<!\d)\d{12}(?!\d)")),
    ("生年月日", re.compile(r"生年月日")),
]


class PersonalInfoWarning(Exception):
    """公開記録に個人情報らしき記述が含まれ、本人の確認が済んでいない場合に送出する"""

    def __init__(self, warnings: List[Dict[str, str]]):
        self.warnings = warnings
        super().__init__("請求文書の特定内容に個人情報の可能性がある記述が含まれています")


def normalize_requested_documents(text: str) -> str:
    return " ".join((text or "").split())


def scan_personal_info(text: str) -> List[Dict[str, str]]:
    """オンチェーンに平文で載せる前に、個人情報らしき記述を検出する。"""
    found = []
    for label, pattern in _PERSONAL_INFO_PATTERNS:
        for m in pattern.finditer(text or ""):
            found.append({"type": label, "match": m.group(0)})
    return found


EAS_EXPLORER_HOSTS = {
    8453: "https://base.easscan.org",
    84532: "https://base-sepolia.easscan.org",
}


class AttestationRecord(BaseModel):
    """EAS準拠のオンチェーンアテステーション証明書"""
    uid: str                      # 0x... アテステーション一意識別子
    schema_uid: str               # EAS スキーマUID
    record_id: str                # 開示請求レコードID
    title: str                    # 請求件名
    authority: str                # 対象機関・自治体
    document_hash: str            # 0x... 文書本文のKeccak/SHA256ハッシュ
    attester: str                 # 公証アテスターのウォレットアドレス
    recipient: str                # 請求者のウォレットアドレス (または匿名市民代理アドレス)
    timestamp: int                # Unix timestamp（オンチェーン記録時刻）
    formatted_date: str           # JST/UTC フォーマット日時
    legal_basis: str              # 根拠法令・条例
    requested_documents: str = "" # オンチェーンに平文で記録した請求文書の特定内容（非公開の請求では空）
    publish_plaintext: bool = False
    revocable: bool = False       # 開示請求の記録は撤回不能
    network: str                  # 刻印ネットワーク
    chain_id: int
    tx_hash: str                  # オンチェーントランザクションハッシュ
    block_number: int             # ブロック番号
    explorer_url: str             # EASスキャンまたはブロックスキャンのURL
    is_valid: bool = True


# ---------------------------------------------------------------------------
# 索引（インデックス）の永続化
#
# 正本はチェーン（EAS）。ここに保存するのは、チェーンにない付帯情報だけ
# （UIDと請求IDの対応・タイトル・取引ID・発行したユーザー）。請求書全文・氏名・申請番号は保存しない。
# 読み出し時は必ずチェーンと照合するため、索引が改ざんされても偽の記録は表示されない。
# 本番は Firestore、認証情報のない開発環境のみローカルJSON（storage_backend.use_firestore）。
# ---------------------------------------------------------------------------
INDEX_COLLECTION = "attestation_index"
RECORD_ID_COLLECTION = "attestation_record_ids"


class AttestationIndexEntry(BaseModel):
    uid: str
    record_id: str
    title: str
    chain_id: int
    tx_hash: str
    block_number: int
    owner_user_id: Optional[str] = None
    created_at: str


def _load_json_index() -> Dict[str, Dict[str, dict]]:
    try:
        with open(ATTESTATION_STORAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"by_uid": {}, "by_record_id": {}}
    data.setdefault("by_uid", {})
    data.setdefault("by_record_id", {})
    return data


def _save_json_index(data: Dict[str, Dict[str, dict]]):
    os.makedirs(os.path.dirname(ATTESTATION_STORAGE_PATH), exist_ok=True)
    with open(ATTESTATION_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_index(entry: AttestationIndexEntry):
    if use_firestore():
        from firebase_client import get_firestore_client

        db = get_firestore_client()
        db.collection(INDEX_COLLECTION).document(entry.uid.lower()).set(entry.model_dump())
        db.collection(RECORD_ID_COLLECTION).document(entry.record_id).set({"uid": entry.uid})
        return
    data = _load_json_index()
    data["by_uid"][entry.uid.lower()] = entry.model_dump()
    data["by_record_id"][entry.record_id] = entry.uid.lower()
    _save_json_index(data)


def _get_index(uid_or_record_id: str) -> Optional[AttestationIndexEntry]:
    key = uid_or_record_id.strip()
    if use_firestore():
        from firebase_client import get_firestore_client

        db = get_firestore_client()
        if not _is_uid(key):
            ref = db.collection(RECORD_ID_COLLECTION).document(key).get()
            if not ref.exists:
                return None
            key = ref.to_dict()["uid"]
        doc = db.collection(INDEX_COLLECTION).document(key.lower()).get()
        return AttestationIndexEntry(**doc.to_dict()) if doc.exists else None
    data = _load_json_index()
    uid = key.lower() if _is_uid(key) else data["by_record_id"].get(key)
    entry = data["by_uid"].get(uid) if uid else None
    return AttestationIndexEntry(**entry) if entry else None


def _all_index() -> List[AttestationIndexEntry]:
    if use_firestore():
        from firebase_client import get_firestore_client

        docs = get_firestore_client().collection(INDEX_COLLECTION).stream()
        return [AttestationIndexEntry(**d.to_dict()) for d in docs]
    return [AttestationIndexEntry(**v) for v in _load_json_index()["by_uid"].values()]


def _is_uid(value: str) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", value))


def _expected_attester() -> str:
    """Civic Lens の公証アドレス（台帳の表示と同じ判定を使う）"""
    from onchain_ledger import ledger_config

    return ledger_config()["attester"]


def compute_document_hash(content: str) -> str:
    """請求文書テキストの暗号学的 bytes32 ハッシュを算出 (0xプレフィックス付き)"""
    h = hashlib.sha256(content.strip().encode('utf-8')).hexdigest()
    return f"0x{h}"


def issue_attestation(
    record_id: str,
    title: str,
    content: str,
    authority: str,
    legal_basis: str = "情報公開法・各自治体情報公開条例",
    user_wallet_address: Optional[str] = None,
    requested_documents: str = "",
    publish_plaintext: bool = False,
    acknowledge_warnings: bool = False,
    owner_user_id: Optional[str] = None,
) -> AttestationRecord:
    """開示請求書に対する EAS オンチェーン存在証明（タイムスタンプ）を実チェーンに発行する。

    publish_plaintext=True の場合のみ、requested_documents（請求する公文書の特定内容）を
    平文でオンチェーンに記録する。個人情報らしき記述が検出された場合は、
    acknowledge_warnings=True（本人が確認済み）でない限り PersonalInfoWarning を送出する。
    非公開の場合、requestedDocuments は空文字で記録され、内容はハッシュにのみ含まれる。

    実チェーンへの接続 (CHAIN_RPC_URL / CHAIN_PRIVATE_KEY / EAS_SCHEMA_UID) が
    未設定の場合は ChainClientNotConfigured を送出する。疑似tx_hashへの
    フォールバックは行わない — 本モジュールが返す証跡は常に実オンチェーン結果のみ。
    """
    public_documents = ""
    if publish_plaintext:
        public_documents = normalize_requested_documents(requested_documents)
        if not public_documents:
            raise ValueError("公開する場合は、請求する公文書の特定内容を入力してください。")
        if len(public_documents.encode("utf-8")) > MAX_REQUESTED_DOCUMENTS_BYTES:
            raise ValueError(
                f"請求文書の特定内容が長すぎます（上限 {MAX_REQUESTED_DOCUMENTS_BYTES} バイト）。"
            )
        warnings = scan_personal_info(public_documents)
        if warnings and not acknowledge_warnings:
            raise PersonalInfoWarning(warnings)

    doc_hash = compute_document_hash(content)
    now_ts = int(time.time())
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 請求者ウォレット未指定時は EAS の慣例どおり宛先なし（ゼロアドレス）で記録する
    recipient = user_wallet_address or ZERO_ADDRESS

    encoded_data = abi_encode(
        ["string", "string", "string", "bytes32", "uint256", "string"],
        [record_id, authority, public_documents, bytes.fromhex(doc_hash[2:]), now_ts, legal_basis],
    )

    schema_uid = os.getenv("EAS_SCHEMA_UID")
    chain_result = submit_attestation_onchain(
        schema_uid=schema_uid,
        recipient=recipient,
        encoded_data=encoded_data,
        revocable=False,
    )

    chain_id = chain_result["chain_id"]
    explorer_host = EAS_EXPLORER_HOSTS.get(chain_id, "https://easscan.org")
    explorer_url = f"{explorer_host}/attestation/view/{chain_result['uid']}"

    record = AttestationRecord(
        uid=chain_result["uid"],
        schema_uid=schema_uid,
        record_id=record_id,
        title=title,
        authority=authority,
        document_hash=doc_hash,
        attester=chain_result["attester"],
        recipient=recipient,
        timestamp=now_ts,
        formatted_date=now_iso,
        legal_basis=legal_basis,
        requested_documents=public_documents,
        publish_plaintext=publish_plaintext,
        revocable=False,
        network=f"Chain ID {chain_id}",
        chain_id=chain_id,
        tx_hash=chain_result["tx_hash"],
        block_number=chain_result["block_number"],
        explorer_url=explorer_url,
        is_valid=True
    )

    _save_index(AttestationIndexEntry(
        uid=record.uid,
        record_id=record_id,
        title=title,
        chain_id=chain_id,
        tx_hash=record.tx_hash,
        block_number=record.block_number,
        owner_user_id=owner_user_id,
        created_at=now_iso,
    ))

    return record


def build_verification_kit(record: AttestationRecord, content: str) -> Dict:
    """請求者本人に渡す「検証キット」。原本（氏名・申請番号等を含む全文）はサーバーに残さず、本人が保管する。"""
    return {
        "format": "civic-lens-verification-kit/v1",
        "note": (
            "この原本（original_text）の前後の空白を除いたUTF-8のSHA-256が、オンチェーンの documentHash と一致すれば、"
            "記録日時にこの内容の文書が存在したことを検証できます。原本には個人情報が含まれるため、取り扱いに注意してください。"
        ),
        "uid": record.uid,
        "record_id": record.record_id,
        "explorer_url": record.explorer_url,
        "chain_id": record.chain_id,
        "schema_uid": record.schema_uid,
        "attester": record.attester,
        "authority": record.authority,
        "legal_basis": record.legal_basis,
        "requested_documents": record.requested_documents,
        "document_hash": record.document_hash,
        "hash_algorithm": "sha256(utf-8, trimmed)",
        "recorded_at": record.formatted_date,
        "original_text": content,
    }


def get_attestation(uid_or_record_id: str) -> Optional[AttestationRecord]:
    """UID または record_id から記録を取得する。内容は必ずチェーンから読み出し、

    スキーマと記録者（Civic Lens 公証アドレス）が一致しない・撤回済み・チェーン上に存在しない場合は None。
    """
    entry = _get_index(uid_or_record_id)
    uid = entry.uid if entry else (uid_or_record_id if _is_uid(uid_or_record_id) else None)
    if not uid:
        return None
    onchain = fetch_attestation_onchain(uid)
    schema_uid = os.getenv("EAS_SCHEMA_UID") or ""
    if (
        onchain is None
        or onchain["schema"].lower() != schema_uid.lower()
        or onchain["attester"].lower() != _expected_attester().lower()
        or onchain["revocation_time"]
    ):
        return None

    record_id, authority, requested, doc_hash, _, legal_basis = abi_decode(
        ["string", "string", "string", "bytes32", "uint256", "string"], onchain["data"]
    )
    chain_id = onchain["chain_id"]
    explorer_host = EAS_EXPLORER_HOSTS.get(chain_id, "https://easscan.org")
    return AttestationRecord(
        uid=onchain["uid"],
        schema_uid=onchain["schema"],
        record_id=record_id,
        title=entry.title if entry else record_id,
        authority=authority,
        document_hash="0x" + doc_hash.hex(),
        attester=onchain["attester"],
        recipient=onchain["recipient"],
        timestamp=onchain["time"],
        formatted_date=datetime.fromtimestamp(onchain["time"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        legal_basis=legal_basis,
        requested_documents=requested,
        publish_plaintext=bool(requested),
        revocable=onchain["revocable"],
        network=f"Chain ID {chain_id}",
        chain_id=chain_id,
        tx_hash=entry.tx_hash if entry else "",
        block_number=entry.block_number if entry else 0,
        explorer_url=f"{explorer_host}/attestation/view/{onchain['uid']}",
        is_valid=True,
    )


def verify_attestation(uid_or_record_id: str, content_to_verify: str) -> Dict:
    """提出された請求文書が、チェーン上に記録されたハッシュと1文字の相違もなく一致するか検証"""
    record = get_attestation(uid_or_record_id)
    if not record:
        return {
            "verified": False,
            "error": "チェーン上に該当する記録が見つかりません。"
        }

    current_hash = compute_document_hash(content_to_verify)
    is_match = (current_hash.lower() == record.document_hash.lower())

    return {
        "verified": is_match,
        "uid": record.uid,
        "recorded_hash": record.document_hash,
        "calculated_hash": current_hash,
        "timestamp": record.timestamp,
        "formatted_date": record.formatted_date,
        "authority": record.authority,
        "network": record.network,
        "explorer_url": record.explorer_url,
        "legal_notary_status": "VALID_EXISTENCE_PROOF" if is_match else "HASH_MISMATCH_TAMPERED",
        "message": "原本証明完了: オンチェーンに記録されたタイムスタンプおよびハッシュと完全一致します。" if is_match else "警告: 文書内容がアテステーション発行時から改ざんまたは変更されています。"
    }


def list_all_attestations() -> List[AttestationRecord]:
    """索引にあり、かつチェーン上で確認できた記録の一覧（新しい順）"""
    result = []
    for entry in _all_index():
        record = get_attestation(entry.uid)
        if record:
            result.append(record)
    return sorted(result, key=lambda x: x.timestamp, reverse=True)
