"""data/gyofuku_cases.json の各認容事例をGemini Embeddings APIでベクトル化し、
data/gyofuku_cases_embeddings.json に保存するビルドスクリプト。

precedent_cases.find_relevant_precedents_by_embedding() がここで生成したベクトルを
読み込み、クエリ（条例名・不開示理由・処分庁）とのコサイン類似度検索に使う。

## 設計方針
- 本体データ(data/gyofuku_cases.json)を肥大化させないよう、ベクトルは別ファイルに
  分離し case_id で紐付ける。
- 既に計算済みの case_id は再計算をスキップするキャッシュ設計にする（無駄な
  API呼び出し・課金を避けるため）。事例テキストが変わった場合のみ再計算する
  （テキストのハッシュを保存して比較する）。
- GEMINI_API_KEY が未設定の場合は何もせず終了する（CI等でsecrets未設定でも
  ジョブ全体を失敗させないため）。

## 使い方
    export GEMINI_API_KEY="your-gemini-api-key"
    .venv/bin/python scripts/build_precedent_embeddings.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from precedent_cases import (  # noqa: E402
    DATA_PATH,
    EMBEDDINGS_PATH,
    EMBEDDING_MODEL,
    case_text_for_embedding,
)

WAIT_BETWEEN_REQUESTS_SEC = 0.5


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_cases() -> list[dict]:
    if not DATA_PATH.exists():
        print(f"データが見つかりません: {DATA_PATH}")
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("cases", [])


def _load_existing_embeddings() -> dict:
    if not EMBEDDINGS_PATH.exists():
        return {}
    try:
        with open(EMBEDDINGS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload.get("embeddings", {})


def main() -> int:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY(またはGOOGLE_API_KEY)が未設定のため、埋め込み計算をスキップします。")
        return 0

    try:
        from google import genai
    except ImportError:
        print("google-genai がインストールされていません。埋め込み計算をスキップします。")
        return 0

    cases = _load_cases()
    if not cases:
        print("対象事例が0件のため終了します。")
        return 0

    existing = _load_existing_embeddings()
    client = genai.Client(api_key=api_key)

    updated = 0
    skipped = 0
    failed = 0

    for case in cases:
        case_id = case.get("case_id", "")
        if not case_id:
            continue
        text = case_text_for_embedding(case)
        if not text:
            continue
        text_hash = _text_hash(text)

        cached = existing.get(case_id)
        if cached and cached.get("text_hash") == text_hash and cached.get("vector"):
            skipped += 1
            continue

        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
            )
            vector = list(response.embeddings[0].values)
        except Exception as e:
            print(f"  [失敗] {case_id}: {e}")
            failed += 1
            continue

        existing[case_id] = {
            "vector": vector,
            "text_hash": text_hash,
            "model": EMBEDDING_MODEL,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        updated += 1
        print(f"  [更新] {case_id} ({len(vector)}次元)")
        time.sleep(WAIT_BETWEEN_REQUESTS_SEC)

    payload = {
        "model": EMBEDDING_MODEL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(existing),
        "embeddings": existing,
    }
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"完了: 更新{updated}件 / スキップ(キャッシュ済){skipped}件 / 失敗{failed}件 -> {EMBEDDINGS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
