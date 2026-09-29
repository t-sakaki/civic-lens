"""Civic Lens — オンチェーン開示請求への市民リアクション（オフチェーン）

台帳（オンチェーン）は事実だけを記録し、市民の反応は意見としてこちらに分けて保持する。
攻撃の場にならないよう、リアクションは行動につながる3種類に限定し、自由記述は受け付けない。

  👀 watch        見守る       — この請求への行政の対応を見守っている
  🙋 want_to_know 私も知りたい — 自分もこの文書に関心がある
  🔁 fork_local   自分の自治体でも — 同じ請求を別の自治体に出したい

1人1記録につき各種類1回まで（ログイン、またはウォレットの無料署名（personal_sign、ガス代なし）が必要、トグル）。
ウォレット署名はcivic-disclosure-tip（ETHGlobal Tokyo 2026）と同じ方式で、サーバーは署名の検証のみ行い代理署名しない。誰が反応したかは公開せず件数のみ返す。
保存するのはユーザーIDそのものではなく、記録UIDと組み合わせたハッシュ（記録をまたいだ突合を防ぐ）。

保存先は本番では Firestore（`ledger_reactions` コレクション、ドキュメントID = 記録UID）。
認証情報のない開発環境のみローカルJSONを使う（storage_backend.use_firestore）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional

from storage_backend import use_firestore

if os.getenv("VERCEL"):
    _STORE_PATH = Path("/tmp/civic_lens_data") / "ledger_reactions.json"
else:
    _STORE_PATH = Path(__file__).resolve().parent / "data" / "ledger_reactions.json"
_LOCK = threading.Lock()

COLLECTION = "ledger_reactions"
REACTION_TYPES = ["watch", "want_to_know", "fork_local"]
REACTION_LABELS = {
    "watch": {"emoji": "👀", "label": "見守る"},
    "want_to_know": {"emoji": "🙋", "label": "私も知りたい"},
    "fork_local": {"emoji": "🔁", "label": "自分の自治体でも"},
}


def _load() -> Dict[str, Dict[str, list]]:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: Dict[str, Dict[str, list]]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _reactor_key(uid: str, user_id: str) -> str:
    return hashlib.sha256(f"{uid.lower()}:{user_id}".encode("utf-8")).hexdigest()


def _summary(entry: Dict[str, list], uid: str, user_id: Optional[str]) -> Dict[str, dict]:
    key = _reactor_key(uid, user_id) if user_id else None
    return {
        t: {**REACTION_LABELS[t], "count": len(entry.get(t, [])), "mine": bool(key and key in entry.get(t, []))}
        for t in REACTION_TYPES
    }


def _doc(uid: str):
    from firebase_client import get_firestore_client

    return get_firestore_client().collection(COLLECTION).document(uid.lower())


def _read_entry(uid: str) -> Dict[str, list]:
    if use_firestore():
        snap = _doc(uid).get()
        return (snap.to_dict() or {}) if snap.exists else {}
    return _load().get(uid.lower(), {})


WALLET_MESSAGE_MAX_AGE_SECONDS = 300


def wallet_reactor_id(address: str) -> str:
    """ウォレット署名で反応した人の識別子。ログインユーザーIDと名前空間が衝突しないよう接頭辞を付ける。"""
    return f"wallet:{address.lower()}"


def build_wallet_message(uid: str, reaction: str, timestamp: int) -> str:
    """ブラウザ（templates/ledger.html）が署名する文言と完全に一致させること。"""
    return (
        "Civic Lens ledger reaction (no gas, not a transaction)\n"
        f"record: {uid.lower()}\n"
        f"reaction: {reaction}\n"
        f"timestamp: {timestamp}"
    )


def verify_wallet_signature(uid: str, reaction: str, address: str, timestamp: int, signature: str) -> str:
    """署名を検証し、リアクション用の識別子（wallet:0x...）を返す。ガス代不要のpersonal_sign。"""
    import time

    from eth_account import Account
    from eth_account.messages import encode_defunct

    if reaction not in REACTION_TYPES:
        raise ValueError(f"未対応のリアクションです: {reaction}")
    if abs(time.time() - timestamp) > WALLET_MESSAGE_MAX_AGE_SECONDS:
        raise ValueError("署名の有効期限が切れています。もう一度お試しください。")
    try:
        recovered = Account.recover_message(
            encode_defunct(text=build_wallet_message(uid, reaction, timestamp)), signature=signature
        )
    except Exception:
        raise ValueError("署名を検証できませんでした。")
    if recovered.lower() != address.lower():
        raise ValueError("署名がウォレットアドレスと一致しません。")
    return wallet_reactor_id(recovered)


def get_reactions(uid: str, user_id: Optional[str] = None) -> Dict[str, dict]:
    return _summary(_read_entry(uid), uid, user_id)


def toggle_reaction(uid: str, user_id: str, reaction: str) -> Dict[str, dict]:
    if reaction not in REACTION_TYPES:
        raise ValueError(f"未対応のリアクションです: {reaction}")
    key = _reactor_key(uid, user_id)
    if use_firestore():
        from firebase_admin import firestore

        already = key in _read_entry(uid).get(reaction, [])
        op = firestore.ArrayRemove([key]) if already else firestore.ArrayUnion([key])
        _doc(uid).set({reaction: op}, merge=True)  # 配列操作はサーバー側で原子的に適用される
        return _summary(_read_entry(uid), uid, user_id)
    with _LOCK:
        data = _load()
        entry = data.setdefault(uid.lower(), {})
        reactors = entry.setdefault(reaction, [])
        if key in reactors:
            reactors.remove(key)
        else:
            reactors.append(key)
        _save(data)
    return _summary(entry, uid, user_id)
