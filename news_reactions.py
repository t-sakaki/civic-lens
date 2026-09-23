"""News Anger Records

ニュース怒り再現エージェントが分析した結果を記録として永続化し、
一覧（履歴メニュー）で振り返れるようにする。あわせて、擬似的な市民の声への
「いいね」等のリアクション（共感の記録）も保持する。

永続化はデモ用途のためJSONファイルへのシンプルな読み書きとする。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_BUNDLED_PATH = Path(__file__).resolve().parent / "data" / "news_anger_records.json"
# Vercel等のサーバーレス環境ではデプロイ先が読み取り専用のため /tmp に書き込む
# （初回は同梱の記録データを読み込み、以降は /tmp 側を更新する）
if os.getenv("VERCEL"):
    _STORE_PATH = Path("/tmp/civic_lens_data") / "news_anger_records.json"
else:
    _STORE_PATH = _BUNDLED_PATH
_LOCK = threading.Lock()

REACTION_TYPES = ["heart", "angry", "shock"]
REACTION_EMOJI = {"heart": "❤️", "angry": "😡", "shock": "😳"}


def make_news_id(link: str) -> str:
    """ニュースリンクから安定した記録IDを生成する"""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def _load() -> Dict[str, Any]:
    path = _STORE_PATH if _STORE_PATH.exists() else _BUNDLED_PATH
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    # Vercel等のサーバーレス環境では /tmp 以外が読み取り専用のファイルシステムのため、
    # 書き込みが OSError（Read-only file system）で失敗することがある。
    # ここで失敗しても分析結果自体は呼び出し元にそのまま返せるよう、握りつぶして継続する
    # （履歴・リアクションの永続化だけが失われる）。他のFirestore依存箇所と同様の方針。
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _STORE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[news_reactions] 保存に失敗しました（読み取り専用ファイルシステムの可能性）: {e}")


def record_analysis(
    news_id: str,
    source_news: Dict[str, Any],
    key_points: List[str],
    pseudo_citizen_voice: str,
    disclaimer: str,
    anger_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """分析結果を記録する（既存レコードがあればリアクション数は維持して内容だけ更新）"""
    with _LOCK:
        data = _load()
        existing = data.get(news_id)
        reactions = existing["reactions"] if existing else {r: 0 for r in REACTION_TYPES}

        record = {
            "news_id": news_id,
            "source_news": source_news,
            "key_points": key_points,
            "pseudo_citizen_voice": pseudo_citizen_voice,
            "pseudo_citizen_voice_disclaimer": disclaimer,
            "anger_analysis": anger_analysis,
            "reactions": reactions,
            "created_at": existing["created_at"] if existing else datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        data[news_id] = record
        _save(data)
        return record


def add_reaction(news_id: str, reaction: str) -> Optional[Dict[str, Any]]:
    """指定した記録にリアクション（共感）を1つ加算する"""
    if reaction not in REACTION_TYPES:
        reaction = "heart"
    with _LOCK:
        data = _load()
        record = data.get(news_id)
        if record is None:
            return None
        record.setdefault("reactions", {r: 0 for r in REACTION_TYPES})
        record["reactions"][reaction] = record["reactions"].get(reaction, 0) + 1
        data[news_id] = record
        _save(data)
        return record


def get_record(news_id: str) -> Optional[Dict[str, Any]]:
    return _load().get(news_id)


def list_records(limit: int = 50) -> List[Dict[str, Any]]:
    """新しい順に記録一覧を返す（履歴メニュー用）"""
    data = _load()
    records = sorted(data.values(), key=lambda r: r.get("updated_at", ""), reverse=True)
    return records[:limit]
