"""Civic Lens — 開示請求・不服審査請求へのコメント

プロジェクトメンバー同士が1件の請求記録について議論できるようにする機能。
保存先はFirestore（`record_comments`コレクション）。
"""
import secrets
from typing import List
from datetime import datetime
from pydantic import BaseModel

from firebase_client import get_firestore_client

COLLECTION = "record_comments"


class Comment(BaseModel):
    comment_id: str
    record_id: str
    user_id: str
    username: str
    text: str
    created_at: str


def _collection():
    return get_firestore_client().collection(COLLECTION)


def add_comment(record_id: str, user_id: str, username: str, text: str) -> Comment:
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("コメント内容を入力してください")
    if len(clean_text) > 2000:
        raise ValueError("コメントは2000文字以内で入力してください")

    comment = Comment(
        comment_id=f"cmt-{secrets.token_hex(8)}",
        record_id=record_id,
        user_id=user_id,
        username=username,
        text=clean_text,
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    _collection().document(comment.comment_id).set(comment.model_dump())
    return comment


def get_comments(record_id: str) -> List[Comment]:
    docs = _collection().where("record_id", "==", record_id).stream()
    comments = [Comment(**d.to_dict()) for d in docs]
    return sorted(comments, key=lambda c: c.created_at)
