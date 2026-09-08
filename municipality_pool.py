"""Civic Lens — 自治体情報公開制度プール

data/authorities/*.json に未収録の自治体について、バックグラウンドエージェントが
調査した情報公開条例の概要をFirestoreにプールし、次回以降のアクセスで再利用する。

フロー:
1. ブラウザGeolocationで緯度経度を取得 → geolocation.detect_municipality() で市区町村を特定
2. 静的データ（ordinance_data.AUTHORITIES）に無ければ、このプールを参照
3. プールにも無ければ status="researching" で仮登録し、バックグラウンドタスクで
   gmi_client.research_municipality_disclosure_system() を呼び出して調査・保存する
"""
from typing import Optional, Dict
from datetime import datetime
from pydantic import BaseModel

from firebase_client import get_firestore_client

COLLECTION = "municipality_pool"


class PooledMunicipality(BaseModel):
    """プールされた自治体の情報公開制度情報"""
    muni_code: str
    prefecture: str
    municipality: str
    full_name: str
    status: str  # "researching" / "ready" / "failed"
    ordinance_name: Optional[str] = None
    authority_type: Optional[str] = None
    request_deadline_days: Optional[int] = None
    extension_days: Optional[int] = None
    review_period_days: Optional[int] = None
    non_disclosure_grounds: list = []
    review_authority: Optional[str] = None
    contact: Optional[str] = None
    is_mock: bool = False
    created_at: str
    updated_at: str


def _doc(muni_code: str):
    return get_firestore_client().collection(COLLECTION).document(muni_code)


def get_pooled(muni_code: str) -> Optional[Dict]:
    """プール済みレコードを取得（存在しなければNone）"""
    snap = _doc(muni_code).get()
    return snap.to_dict() if snap.exists else None


def mark_researching(muni_code: str, prefecture: str, municipality: str, full_name: str) -> Dict:
    """調査開始を仮登録する（既存レコードがあれば上書きしない）"""
    existing = get_pooled(muni_code)
    if existing:
        return existing

    now = datetime.utcnow().isoformat() + "Z"
    record = PooledMunicipality(
        muni_code=muni_code,
        prefecture=prefecture,
        municipality=municipality,
        full_name=full_name,
        status="researching",
        created_at=now,
        updated_at=now,
    ).model_dump()
    _doc(muni_code).set(record)
    return record


def save_research_result(muni_code: str, research: Dict) -> Dict:
    """バックグラウンド調査エージェントの結果を保存する"""
    existing = get_pooled(muni_code) or {}
    now = datetime.utcnow().isoformat() + "Z"

    record = {
        **existing,
        "status": "ready",
        "ordinance_name": research.get("ordinance_name"),
        "authority_type": research.get("authority_type"),
        "request_deadline_days": research.get("request_deadline_days", 30),
        "extension_days": research.get("extension_days", 30),
        "review_period_days": research.get("review_period_days", 90),
        "non_disclosure_grounds": research.get("non_disclosure_grounds", []),
        "review_authority": research.get("review_authority"),
        "contact": research.get("contact"),
        "is_mock": research.get("is_mock", False),
        "updated_at": now,
    }
    _doc(muni_code).set(record)
    return record


def mark_failed(muni_code: str, error: str) -> Dict:
    """調査失敗を記録する"""
    existing = get_pooled(muni_code) or {}
    now = datetime.utcnow().isoformat() + "Z"
    record = {**existing, "status": "failed", "error": error, "updated_at": now}
    _doc(muni_code).set(record)
    return record
