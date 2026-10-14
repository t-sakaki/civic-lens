"""開示請求の結果フィードバック（自己改善ループ）

1. 生成した開示請求書ごとに、市民が実際の結果（開示 / 一部開示 / 不開示 / 未提出）を記録する。
   保存先は Firestore（コレクション disclosure_outcomes）。使えない環境ではローカルJSONに保存する。
2. 新しい請求書を生成するとき、同じ機関（なければ同じ地域・種別）で「開示」「一部開示」に
   なった過去の請求を数件取り出し、プロンプトに few-shot 例として入れる。

実行時にコードを書き換えることはしない。改善はデータ（記録された結果）だけを通じて行う。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

OUTCOMES = {
    "disclosed": "開示",
    "partial": "一部開示",
    "non_disclosure": "不開示",
    "not_submitted": "未提出",
}
SUCCESS_OUTCOMES = ("disclosed", "partial")
COLLECTION = "disclosure_outcomes"
MAX_EXCERPT = 1200
_lock = threading.Lock()


def _local_path() -> Path:
    env = os.getenv("CIVIC_LENS_OUTCOMES_PATH")
    if env:
        return Path(env)
    base = Path(__file__).parent / "data"
    if os.getenv("VERCEL") or os.getenv("K_SERVICE") or not os.access(base, os.W_OK):
        base = Path("/tmp")
    return base / "disclosure_outcomes.json"


def _firestore():
    try:
        from storage_backend import use_firestore
        if not use_firestore():
            return None
        from firebase_client import get_firestore_client
        return get_firestore_client()
    except Exception as e:  # noqa: BLE001
        print(f"[disclosure_outcomes] Firestore を使えないためローカルJSONに保存します: {e}", file=sys.stderr)
        return None


def _load_local() -> dict:
    p = _local_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_local(data: dict) -> None:
    p = _local_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def record_outcome(
    request_id: str,
    outcome: str,
    authority_key: str,
    authority: str = "",
    region: str = "",
    request_excerpt: str = "",
) -> dict:
    """結果を記録（同じ request_id は上書き。押し直して訂正できる）"""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome は {list(OUTCOMES)} のいずれか: {outcome}")
    if not request_id or len(request_id) > 64:
        raise ValueError("request_id が不正です")
    doc = {
        "request_id": request_id,
        "outcome": outcome,
        "outcome_label": OUTCOMES[outcome],
        "authority_key": authority_key or "",
        "authority": authority or "",
        "region": region or "",
        "request_excerpt": (request_excerpt or "")[:MAX_EXCERPT],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db = _firestore()
    if db is not None:
        try:
            db.collection(COLLECTION).document(request_id).set(doc)
            return {**doc, "storage": "firestore"}
        except Exception as e:  # noqa: BLE001
            print(f"[disclosure_outcomes] Firestore 書き込み失敗、ローカルに保存: {e}", file=sys.stderr)
    with _lock:
        data = _load_local()
        data[request_id] = doc
        _save_local(data)
    return {**doc, "storage": "local"}


def _all_outcomes() -> list[dict]:
    db = _firestore()
    if db is not None:
        try:
            return [d.to_dict() for d in db.collection(COLLECTION).limit(500).stream()]
        except Exception as e:  # noqa: BLE001
            print(f"[disclosure_outcomes] Firestore 読み込み失敗、ローカルを使用: {e}", file=sys.stderr)
    return list(_load_local().values())


def find_similar_successes(authority_key: str, region: str = "", limit: int = 3) -> list[dict]:
    """同じ機関 → 同じ地域 の順で、開示・一部開示になった過去の請求を返す（新しい順）"""
    cands = [d for d in _all_outcomes() if d.get("outcome") in SUCCESS_OUTCOMES and d.get("request_excerpt")]

    def tier(d: dict) -> int:
        if authority_key and d.get("authority_key") == authority_key:
            return 0
        r, a = d.get("region", ""), d.get("authority", "")
        if region and ((r and (region in r or r in region)) or (a and (region in a or a in region))):
            return 1
        return 2

    relevant = [d for d in cands if tier(d) < 2]
    relevant.sort(key=lambda d: d.get("updated_at", ""), reverse=True)  # 新しい順
    relevant.sort(key=lambda d: (tier(d), 0 if d.get("outcome") == "disclosed" else 1))  # 安定ソート
    return relevant[:limit]


def format_few_shot(cases: list[dict]) -> str:
    """プロンプトに入れる few-shot 例のテキスト（なければ空文字）"""
    if not cases:
        return ""
    lines = ["【参考: 過去に同じ機関・地域で開示につながった請求（文書の特定の仕方を参考にすること。内容をそのまま写さない）】"]
    for i, c in enumerate(cases, 1):
        lines.append(f"--- 例{i}（{c.get('authority') or c.get('authority_key')} / 結果: {c.get('outcome_label')}）---")
        lines.append(c.get("request_excerpt", "")[:600])
    return "\n".join(lines) + "\n"


def stats(authority_key: Optional[str] = None) -> dict:
    rows = [d for d in _all_outcomes() if not authority_key or d.get("authority_key") == authority_key]
    counts = {k: 0 for k in OUTCOMES}
    for d in rows:
        if d.get("outcome") in counts:
            counts[d["outcome"]] += 1
    submitted = sum(v for k, v in counts.items() if k != "not_submitted")
    rate = (counts["disclosed"] + counts["partial"]) / submitted if submitted else None
    return {"counts": counts, "total": len(rows), "success_rate": rate}