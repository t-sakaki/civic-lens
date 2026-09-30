"""News Anger Records

ニュース怒り再現エージェントが分析した結果を記録として永続化し、
一覧（履歴メニュー）で振り返れるようにする。あわせて、擬似的な市民の声への
「いいね」等のリアクション（共感の記録）も保持する。

保存先は本番（Cloud Run等）ではFirestore（`news_anger_records`コレクション、ドキュメントID = 記録ID）。
Cloud Runのコンテナはインスタンスごとにファイルシステムが別で再起動でも消えるため、
JSONファイル保存だと「分析した直後にシェア/リアクションしても記録が見つからない」ことがあった。
Firestoreの認証情報がない開発環境のみ、従来どおりローカルJSONを使う（storage_backend.use_firestore）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage_backend import use_firestore

_BUNDLED_PATH = Path(__file__).resolve().parent / "data" / "news_anger_records.json"
# Vercel等のサーバーレス環境ではデプロイ先が読み取り専用のため /tmp に書き込む
# （初回は同梱の記録データを読み込み、以降は /tmp 側を更新する）
if os.getenv("VERCEL"):
    _STORE_PATH = Path("/tmp/civic_lens_data") / "news_anger_records.json"
else:
    _STORE_PATH = _BUNDLED_PATH
_LOCK = threading.Lock()

COLLECTION = "news_anger_records"
REACTION_TYPES = ["heart", "angry", "shock"]
REACTION_EMOJI = {"heart": "❤️", "angry": "😡", "shock": "😳"}


def make_news_id(link: str, theme: Optional[str] = None) -> str:
    """ニュースリンク（と怒りエージェント）から安定した記録IDを生成する

    同じ記事を複数のSDGsエージェントが分析できるよう、themeが一般以外の場合は
    エージェントごとに別の記録IDにする（themeなし/generalは従来のIDと同一）。
    """
    key = link if not theme or theme == "general" else f"{link}#{theme}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _load() -> Dict[str, Any]:
    path = _STORE_PATH if _STORE_PATH.exists() else _BUNDLED_PATH
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_bundled() -> Dict[str, Any]:
    """リポジトリ同梱のサンプル記録（Firestoreには入っていないので読み取り時に併せて見せる）"""
    try:
        with _BUNDLED_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    # JSON保存はFirestore認証情報のない開発環境用。読み取り専用FSで失敗しても
    # 分析結果自体は呼び出し元に返せるよう、握りつぶして継続する。
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _STORE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[news_reactions] 保存に失敗しました（読み取り専用ファイルシステムの可能性）: {e}")


def _collection():
    from firebase_client import get_firestore_client

    return get_firestore_client().collection(COLLECTION)


def _get(news_id: str) -> Optional[Dict[str, Any]]:
    if use_firestore():
        snap = _collection().document(news_id).get()
        if snap.exists:
            return snap.to_dict()
        return _load_bundled().get(news_id)
    return _load().get(news_id)


def _put(record: Dict[str, Any]) -> None:
    if use_firestore():
        _collection().document(record["news_id"]).set(record)
        return
    data = _load()
    data[record["news_id"]] = record
    _save(data)


def _all() -> Dict[str, Any]:
    if use_firestore():
        merged = _load_bundled()
        merged.update({doc.id: doc.to_dict() for doc in _collection().stream()})
        return merged
    return _load()


def record_analysis(
    news_id: str,
    source_news: Dict[str, Any],
    key_points: List[str],
    pseudo_citizen_voice: str,
    disclaimer: str,
    anger_analysis: Optional[Dict[str, Any]],
    theme: str = "general",
    region: Optional[str] = None,
    autonomous: bool = False,
    voices: Optional[List[Dict[str, Any]]] = None,
    proposals: Optional[List[Dict[str, Any]]] = None,
    summary: Optional[str] = None,
    overall_anger_level: Optional[int] = None,
) -> Dict[str, Any]:
    """分析結果を記録する（既存レコードがあればリアクション数・既読状態は維持して内容だけ更新）

    theme: どのテーマ別怒り再現エージェントが生成したか（news_anger_agent.NEWS_THEMES のキー）。
      複数エージェントの声を統合した記録は "multi"
    voices: ニュース1件に対して各SDGsエージェントが挙げた声（統合記録のみ）
    proposals: 統合エージェントが提案した開示請求の対象（機関・文書・根拠となるエージェント）
    summary / overall_anger_level: 統合エージェントの所見と怒りレベル
    anger_analysis: 開示請求の分析結果。統合記録ではユーザーが提案を選んだ時点で埋まる（それまでNone）
    autonomous: Cloud Scheduler等からの自律スキャンによる記録か（ユーザー操作による記録ならFalse）
    """
    with _LOCK:
        existing = _get(news_id)
        reactions = existing["reactions"] if existing else {r: 0 for r in REACTION_TYPES}

        record = {
            "news_id": news_id,
            "source_news": source_news,
            "region": region if region is not None else (existing or {}).get("region"),
            "theme": theme,
            "key_points": key_points,
            "pseudo_citizen_voice": pseudo_citizen_voice,
            "pseudo_citizen_voice_disclaimer": disclaimer,
            "anger_analysis": anger_analysis,
            "reactions": reactions,
            "voices": voices if voices is not None else (existing or {}).get("voices", []),
            "proposals": proposals if proposals is not None else (existing or {}).get("proposals", []),
            "summary": summary if summary is not None else (existing or {}).get("summary"),
            "overall_anger_level": overall_anger_level,
            "selected_proposal": (existing or {}).get("selected_proposal"),
            "autonomous": autonomous,
            # 自律スキャンで生成された記録はユーザーがまだ見ていない「未読」として扱う。
            # ユーザー自身の操作による記録（一覧から選んで分析）は既読扱い。
            "read": existing["read"] if existing else (not autonomous),
            "created_at": existing["created_at"] if existing else datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _put(record)
        return record


def update_record(news_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """既存の記録の一部の項目だけを更新する（例: 選んだ提案と、その開示請求分析結果）"""
    with _LOCK:
        record = _get(news_id)
        if record is None:
            return None
        record.update(fields)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        _put(record)
        return record


def mark_read(news_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        record = _get(news_id)
        if record is None:
            return None
        record["read"] = True
        _put(record)
        return record


def count_unread() -> int:
    return sum(1 for r in _all().values() if not r.get("read", True))


def add_reaction(news_id: str, reaction: str) -> Optional[Dict[str, Any]]:
    """指定した記録にリアクション（共感）を1つ加算する"""
    if reaction not in REACTION_TYPES:
        reaction = "heart"
    if use_firestore():
        # 複数インスタンスから同時に押されても取りこぼさないよう、サーバー側でインクリメントする
        from google.api_core.exceptions import NotFound
        from google.cloud import firestore

        ref = _collection().document(news_id)
        if not ref.get().exists:
            seed = _load_bundled().get(news_id)
            if seed is None:
                return None
            ref.set(seed)
        try:
            ref.update({f"reactions.{reaction}": firestore.Increment(1)})
        except NotFound:
            return None
        return ref.get().to_dict()
    with _LOCK:
        record = _get(news_id)
        if record is None:
            return None
        record.setdefault("reactions", {r: 0 for r in REACTION_TYPES})
        record["reactions"][reaction] = record["reactions"].get(reaction, 0) + 1
        _put(record)
        return record


def get_record(news_id: str) -> Optional[Dict[str, Any]]:
    return _get(news_id)


def list_records(limit: int = 50) -> List[Dict[str, Any]]:
    """新しい順に記録一覧を返す（履歴メニュー用）"""
    records = sorted(_all().values(), key=lambda r: r.get("updated_at", ""), reverse=True)
    return records[:limit]
