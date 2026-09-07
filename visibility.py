"""Civic Lens — 開示請求の公開設定

Private / Public 公開設定:
- Private: 自分のための通常開示請求
- Public: 他の市民と共有し、集合知として活用

Publicデータは匿名化され、地域の行政問題を可視化する。

保存先はFirestore（`disclosure_requests`コレクション）。Cloud Runのコンテナは
リクエスト間でファイルシステムを保持しないため、以前のJSONファイル保存方式は
デプロイ・スケールのたびにデータが失われていた。
"""
import os
import hashlib
import uuid
from typing import Optional, List, Dict
from datetime import datetime
from pydantic import BaseModel

from firebase_client import get_firestore_client

COLLECTION = "disclosure_requests"


class DisclosureRequestRecord(BaseModel):
    """開示請求記録"""
    id: str
    user_hash: str  # SHA256(任意のsalt + IP or session_id)
    target_authority: str
    target_authority_name: str
    situation_key: Optional[str] = None
    user_input: str
    request_text: str
    visibility: str  # "private" / "public"
    status: str  # "draft" / "submitted" / "responded" / "rejected"
    result_excerpt: Optional[str] = None  # 開示された内容の抜粋
    created_at: str
    updated_at: str

    # Public用メタデータ
    summary_public: Optional[str] = None  # 公開用サマリ
    category: str = "自治体"  # "自治体" / "警察"
    tags: List[str] = []
    anonymous_user_id: Optional[str] = None  # "市民#0001" のような形式
    user_id: Optional[str] = None  # 認証ユーザーID (usr-...)


def _collection():
    return get_firestore_client().collection(COLLECTION)


def _load_all() -> List[dict]:
    """全レコードを読み込み"""
    return [doc.to_dict() for doc in _collection().stream()]


def _save_one(record: dict):
    """1レコードを保存（作成・更新共通）"""
    _collection().document(record["id"]).set(record)


def _generate_user_hash(session_id: Optional[str] = None, ip: Optional[str] = None) -> str:
    """ユーザの匿名ハッシュ生成"""
    salt = os.getenv("CIVIC_LENS_SALT", "civic-lens-anonymous-2026")
    raw = f"{salt}:{session_id or ip or 'anonymous'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _generate_anonymous_user_id(user_hash: str) -> str:
    """市民#0001 形式の匿名ID"""
    # ハッシュの先頭4文字から数値を生成
    num = int(user_hash[:8], 16) % 99999
    return f"市民#{num:05d}"


def create_record(
    user_input: str,
    request_text: str,
    target_authority: str,
    target_authority_name: str,
    visibility: str,
    situation_key: Optional[str] = None,
    category: str = "自治体",
    tags: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_id: Optional[str] = None,
) -> DisclosureRequestRecord:
    """新規開示請求記録を作成"""
    if visibility not in ("private", "public"):
        raise ValueError(f"visibility must be 'private' or 'public', got '{visibility}'")

    user_hash = _generate_user_hash(session_id, ip)
    anonymous_id = _generate_anonymous_user_id(user_hash) if visibility == "public" else None
    now = datetime.utcnow().isoformat() + "Z"

    record = DisclosureRequestRecord(
        id=str(uuid.uuid4()),
        user_hash=user_hash,
        target_authority=target_authority,
        target_authority_name=target_authority_name,
        situation_key=situation_key,
        user_input=user_input,
        request_text=request_text,
        visibility=visibility,
        status="draft",
        created_at=now,
        updated_at=now,
        category=category,
        tags=tags or [],
        anonymous_user_id=anonymous_id,
        user_id=user_id,
    )

    _save_one(record.model_dump())

    return record


def update_visibility(record_id: str, new_visibility: str, session_id: Optional[str] = None) -> Optional[DisclosureRequestRecord]:
    """公開設定を変更"""
    if new_visibility not in ("private", "public"):
        raise ValueError(f"visibility must be 'private' or 'public', got '{new_visibility}'")

    user_hash = _generate_user_hash(session_id)
    doc_ref = _collection().document(record_id)
    snap = doc_ref.get()

    if not snap.exists or snap.to_dict().get("user_hash") != user_hash:
        return None

    data = snap.to_dict()
    data["visibility"] = new_visibility
    if new_visibility == "public" and not data.get("anonymous_user_id"):
        data["anonymous_user_id"] = _generate_anonymous_user_id(user_hash)
    if new_visibility == "private":
        data["anonymous_user_id"] = None
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    doc_ref.set(data)
    return DisclosureRequestRecord(**data)


def add_result(record_id: str, result_excerpt: str, session_id: Optional[str] = None) -> Optional[DisclosureRequestRecord]:
    """開示結果を追加"""
    user_hash = _generate_user_hash(session_id)
    doc_ref = _collection().document(record_id)
    snap = doc_ref.get()

    if not snap.exists or snap.to_dict().get("user_hash") != user_hash:
        return None

    data = snap.to_dict()
    data["result_excerpt"] = result_excerpt
    data["status"] = "responded"
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    doc_ref.set(data)
    return DisclosureRequestRecord(**data)


def get_public_records(
    category: Optional[str] = None,
    authority: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
) -> List[DisclosureRequestRecord]:
    """公開設定のレコードのみを取得"""
    records = _load_all()

    public_records = []
    for r in records:
        if r["visibility"] != "public":
            continue
        if category and r["category"] != category:
            continue
        if authority and r["authority"] != authority:
            continue
        # tag filtering happens here
        if tag and tag not in r.get("tags", []):
            continue
        # summary_publicを設定（なければuser_inputの先頭100文字）
        if not r.get("summary_public"):
            r["summary_public"] = r["user_input"][:100] + ("..." if len(r["user_input"]) > 100 else "")
        public_records.append(r)

    # 最新順にソート
    public_records.sort(key=lambda x: x["created_at"], reverse=True)

    return [DisclosureRequestRecord(**r) for r in public_records[:limit]]


def get_my_records(session_id: Optional[str] = None, ip: Optional[str] = None) -> List[DisclosureRequestRecord]:
    """自分のレコードを取得"""
    records = _load_all()
    user_hash = _generate_user_hash(session_id, ip)

    my_records = [r for r in records if r["user_hash"] == user_hash]
    my_records.sort(key=lambda x: x["created_at"], reverse=True)

    return [DisclosureRequestRecord(**r) for r in my_records]


def get_public_stats() -> Dict:
    """公開設定の統計"""
    records = _load_all()

    stats = {
        "total_records": len(records),
        "public_count": sum(1 for r in records if r["visibility"] == "public"),
        "private_count": sum(1 for r in records if r["visibility"] == "private"),
        "by_authority": {},
        "by_category": {},
        "by_situation": {},
    }

    for r in records:
        auth = r["target_authority_name"]
        stats["by_authority"][auth] = stats["by_authority"].get(auth, 0) + 1

        cat = r["category"]
        stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1

        if r.get("situation_key"):
            sit = r["situation_key"]
            stats["by_situation"][sit] = stats["by_situation"].get(sit, 0) + 1

    return stats


def get_records_by_user(user_id: str) -> List[DisclosureRequestRecord]:
    """特定ユーザーが作成した請求記録一覧（Private/Public問わず）を取得"""
    records = _load_all()
    user_records = [
        DisclosureRequestRecord(**r)
        for r in records
        if r.get("user_id") == user_id
    ]
    return sorted(user_records, key=lambda x: x.created_at, reverse=True)