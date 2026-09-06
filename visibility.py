"""Civic Lens — 開示請求の公開設定

Private / Public 公開設定:
- Private: 自分のための通常開示請求
- Public: 他の市民と共有し、集合知として活用

Publicデータは匿名化され、地域の行政問題を可視化する。
"""
import os
import json
import hashlib
import uuid
from typing import Optional, List, Dict
from datetime import datetime
from pydantic import BaseModel


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


# ローカル開発用の簡易ストレージ
# 本番ではFirestore / Cloud SQL を使用
DEFAULT_STORAGE_DIR = os.path.join(os.path.dirname(__file__), "data")
if os.getenv("VERCEL"):
    STORAGE_DIR = "/tmp/civic_lens_data"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    STORAGE_PATH = os.path.join(STORAGE_DIR, "disclosure_requests.json")
    default_file = os.path.join(DEFAULT_STORAGE_DIR, "disclosure_requests.json")
    if os.path.exists(default_file) and not os.path.exists(STORAGE_PATH):
        import shutil
        try:
            shutil.copyfile(default_file, STORAGE_PATH)
        except Exception:
            pass
else:
    STORAGE_PATH = os.getenv(
        "CIVIC_LENS_STORAGE",
        os.path.join(DEFAULT_STORAGE_DIR, "disclosure_requests.json")
    )


def _ensure_storage():
    """ストレージディレクトリの確保"""
    os.makedirs(os.path.dirname(STORAGE_PATH), exist_ok=True)
    if not os.path.exists(STORAGE_PATH):
        with open(STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)


def _load_all() -> List[dict]:
    """全レコードを読み込み"""
    _ensure_storage()
    try:
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_all(records: List[dict]):
    """全レコードを保存"""
    _ensure_storage()
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


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
    )

    records = _load_all()
    records.append(record.model_dump())
    _save_all(records)

    return record


def update_visibility(record_id: str, new_visibility: str, session_id: Optional[str] = None) -> Optional[DisclosureRequestRecord]:
    """公開設定を変更"""
    if new_visibility not in ("private", "public"):
        raise ValueError(f"visibility must be 'private' or 'public', got '{new_visibility}'")

    records = _load_all()
    user_hash = _generate_user_hash(session_id)

    for i, r in enumerate(records):
        if r["id"] == record_id and r["user_hash"] == user_hash:
            records[i]["visibility"] = new_visibility
            if new_visibility == "public" and not records[i].get("anonymous_user_id"):
                records[i]["anonymous_user_id"] = _generate_anonymous_user_id(user_hash)
            if new_visibility == "private":
                records[i]["anonymous_user_id"] = None
            records[i]["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _save_all(records)
            return DisclosureRequestRecord(**records[i])

    return None


def add_result(record_id: str, result_excerpt: str, session_id: Optional[str] = None) -> Optional[DisclosureRequestRecord]:
    """開示結果を追加"""
    records = _load_all()
    user_hash = _generate_user_hash(session_id)

    for i, r in enumerate(records):
        if r["id"] == record_id and r["user_hash"] == user_hash:
            records[i]["result_excerpt"] = result_excerpt
            records[i]["status"] = "responded"
            records[i]["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _save_all(records)
            return DisclosureRequestRecord(**records[i])

    return None


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