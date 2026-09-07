"""Civic Lens — フォーク & スター機能

GitHub を情報公開請求に適用:
- フォーク: 他の市民の請求書を参考にする
- スター: 公開請求への共感・支持
- コントリビューター: 市民ごとの活動量

保存先はFirestore（`forks` / `stars`コレクション）。
"""
import os
import uuid
import hashlib
from typing import Optional, List, Dict
from datetime import datetime
from pydantic import BaseModel

from firebase_client import get_firestore_client

FORKS_COLLECTION = "forks"
STARS_COLLECTION = "stars"


class ForkRecord(BaseModel):
    """フォーク記録"""
    id: str
    original_record_id: str
    user_hash: str
    forked_at: str
    customized_authority: Optional[str] = None
    notes: Optional[str] = None  # フォーク時のメモ


class StarRecord(BaseModel):
    """スター記録"""
    id: str
    record_id: str
    user_hash: str
    starred_at: str


class ContributorStats(BaseModel):
    """コントリビューター統計"""
    user_hash: str
    anonymous_user_id: Optional[str]
    total_requests: int
    public_requests: int
    stars_received: int
    forked_count: int  # 自分のリクエストが何回フォークされたか


def _forks_ref():
    return get_firestore_client().collection(FORKS_COLLECTION)


def _stars_ref():
    return get_firestore_client().collection(STARS_COLLECTION)


def _load_forks() -> List[dict]:
    return [doc.to_dict() for doc in _forks_ref().stream()]


def _load_stars() -> List[dict]:
    return [doc.to_dict() for doc in _stars_ref().stream()]


def _generate_user_hash(session_id: Optional[str] = None) -> str:
    """ユーザーのハッシュ"""
    salt = os.getenv("CIVIC_LENS_SALT", "civic-lens-anonymous-2026")
    raw = f"{salt}:{session_id or 'anonymous'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def add_fork(
    original_record_id: str,
    session_id: Optional[str] = None,
    customized_authority: Optional[str] = None,
    notes: Optional[str] = None,
) -> ForkRecord:
    """公開請求をフォーク"""
    user_hash = _generate_user_hash(session_id)

    record = ForkRecord(
        id=str(uuid.uuid4()),
        original_record_id=original_record_id,
        user_hash=user_hash,
        forked_at=datetime.utcnow().isoformat() + "Z",
        customized_authority=customized_authority,
        notes=notes,
    )

    _forks_ref().document(record.id).set(record.model_dump())

    return record


def add_star(
    record_id: str,
    session_id: Optional[str] = None,
) -> Optional[StarRecord]:
    """公開請求にスターを追加"""
    user_hash = _generate_user_hash(session_id)

    # 既にスター済みかチェック
    existing = list(
        _stars_ref()
        .where("record_id", "==", record_id)
        .where("user_hash", "==", user_hash)
        .limit(1)
        .stream()
    )
    if existing:
        return None  # 重複

    record = StarRecord(
        id=str(uuid.uuid4()),
        record_id=record_id,
        user_hash=user_hash,
        starred_at=datetime.utcnow().isoformat() + "Z",
    )

    _stars_ref().document(record.id).set(record.model_dump())

    return record


def remove_star(record_id: str, session_id: Optional[str] = None) -> bool:
    """スターを削除"""
    user_hash = _generate_user_hash(session_id)

    matches = list(
        _stars_ref()
        .where("record_id", "==", record_id)
        .where("user_hash", "==", user_hash)
        .stream()
    )
    for doc in matches:
        doc.reference.delete()

    return len(matches) > 0


def get_record_stats(record_id: str) -> Dict:
    """特定レコードの統計"""
    stars = _load_stars()
    forks = _load_forks()

    star_count = sum(1 for s in stars if s["record_id"] == record_id)
    fork_count = sum(1 for f in forks if f["original_record_id"] == record_id)

    return {
        "stars": star_count,
        "forks": fork_count,
    }


def get_user_actions(record_id: str, session_id: Optional[str] = None) -> Dict:
    """特定レコードに対する特定ユーザーのアクション"""
    user_hash = _generate_user_hash(session_id)

    stars = _load_stars()
    forks = _load_forks()

    has_starred = any(s["record_id"] == record_id and s["user_hash"] == user_hash for s in stars)
    has_forked = any(s["original_record_id"] == record_id and s["user_hash"] == user_hash for s in forks)

    return {
        "hasstarred": has_starred,
        "has_forked": has_forked,
    }


def get_contributor_stats(user_hash: Optional[str] = None, session_id: Optional[str] = None) -> ContributorStats:
    """コントリビューター統計"""
    from visibility import _load_all, _generate_anonymous_user_id

    if not user_hash:
        user_hash = _generate_user_hash(session_id)

    # 自分の開示請求数
    all_requests = _load_all()
    my_requests = [r for r in all_requests if r["user_hash"] == user_hash]
    public_requests = [r for r in my_requests if r["visibility"] == "public"]

    # 自分の開示請求ID一覧
    my_request_ids = {r["id"] for r in my_requests}

    # もらったスター数
    stars = _load_stars()
    stars_received = sum(1 for s in stars if s["record_id"] in my_request_ids)

    # フォークされた数
    forks = _load_forks()
    forked_count = sum(1 for f in forks if f["original_record_id"] in my_request_ids)

    # 匿名ID
    anon_id = None
    if public_requests:
        anon_id = public_requests[0].get("anonymous_user_id") or _generate_anonymous_user_id(user_hash)

    return ContributorStats(
        user_hash=user_hash,
        anonymous_user_id=anon_id,
        total_requests=len(my_requests),
        public_requests=len(public_requests),
        stars_received=stars_received,
        forked_count=forked_count,
    )