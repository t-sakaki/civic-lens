"""Civic Lens — 自治体特定履歴

ブラウザGeolocationによる自治体特定（/api/municipality/detect）の実行履歴を
Firestoreに保存する。ログインユーザーは user_id で、未ログインユーザーは
session_id（フロントエンドがlocalStorageで保持する匿名ID）または IP のハッシュで
紐づけ、visibility.py の user_hash 方式と同じ考え方でマイ履歴を再現できるようにする。
"""
import os
import hashlib
import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

from firebase_client import get_firestore_client

COLLECTION = "municipality_detection_history"


class MunicipalityHistoryRecord(BaseModel):
    """自治体特定履歴の1件"""
    id: str
    user_hash: str
    user_id: Optional[str] = None
    muni_code: str
    prefecture: str
    municipality: str
    full_name: str
    lat: float
    lon: float
    status: str  # "found" / "found_pooled" / "researching" / "failed"
    authority_key: Optional[str] = None
    authority_name: Optional[str] = None
    created_at: str


def _collection():
    return get_firestore_client().collection(COLLECTION)


def _generate_user_hash(session_id: Optional[str] = None, ip: Optional[str] = None) -> str:
    salt = os.getenv("CIVIC_LENS_SALT", "civic-lens-anonymous-2026")
    raw = f"{salt}:{session_id or ip or 'anonymous'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def create_record(
    muni_code: str,
    prefecture: str,
    municipality: str,
    full_name: str,
    lat: float,
    lon: float,
    status: str,
    authority_key: Optional[str] = None,
    authority_name: Optional[str] = None,
    session_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_id: Optional[str] = None,
) -> MunicipalityHistoryRecord:
    """特定結果を履歴として1件保存する"""
    record = MunicipalityHistoryRecord(
        id=str(uuid.uuid4()),
        user_hash=_generate_user_hash(session_id, ip),
        user_id=user_id,
        muni_code=muni_code,
        prefecture=prefecture,
        municipality=municipality,
        full_name=full_name,
        lat=lat,
        lon=lon,
        status=status,
        authority_key=authority_key,
        authority_name=authority_name,
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    _collection().document(record.id).set(record.model_dump())
    return record


def get_history(
    session_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 20,
) -> List[MunicipalityHistoryRecord]:
    """現在のユーザー（ログイン済みなら user_id、未ログインなら session_id/ip のハッシュ）の履歴を取得する"""
    records = [doc.to_dict() for doc in _collection().stream()]

    if user_id:
        mine = [r for r in records if r.get("user_id") == user_id]
    else:
        user_hash = _generate_user_hash(session_id, ip)
        mine = [r for r in records if not r.get("user_id") and r.get("user_hash") == user_hash]

    mine.sort(key=lambda r: r["created_at"], reverse=True)
    return [MunicipalityHistoryRecord(**r) for r in mine[:limit]]
