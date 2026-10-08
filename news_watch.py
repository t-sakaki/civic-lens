"""ニュースの見張り: 購読した地域のニュースをエージェントが監視し、怒りの強いものをブラウザ通知（Web Push）で知らせる。

最上位原則との関係（通知は人を煽る道具にしない）:
- 通知は必ずオプトイン（ブラウザの許可＋購読）。ボタン1つでいつでも解除できる
- 1購読あたり1日の通知数に上限を付け（WATCH_DAILY_CAP）、夜間（22時〜7時 JST）は送らない
- 通知文は「AIのSDGsエージェントが注目している」と明記し、実在の市民の声に見せない
- 通知の先は、保存済みの検討を見せるだけ。開示請求・SNS投稿の判断は本人が行う

保存先は本番（Cloud Run）では Firestore（`push_subscriptions`）。Firestoreがない開発環境はローカルJSON。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from storage_backend import use_firestore

COLLECTION = "push_subscriptions"
_STORE_PATH = Path(__file__).resolve().parent / "data" / "push_subscriptions.json"
_LOCK = threading.Lock()

JST = ZoneInfo("Asia/Tokyo")
QUIET_START_HOUR = 22  # この時刻から
QUIET_END_HOUR = 7  # この時刻まで通知しない
NOTIFIED_KEEP = 50  # 通知済みニュースIDを何件覚えるか

REMARK_MAX = 80
DISCLAIMER = "（AI生成・実在の市民の声ではありません）"


def notify_threshold() -> int:
    return int(os.getenv("WATCH_NOTIFY_THRESHOLD") or "7")


def daily_cap() -> int:
    return int(os.getenv("WATCH_DAILY_CAP") or "3")


def vapid_public_key() -> str:
    return os.getenv("VAPID_PUBLIC_KEY", "")


def push_enabled() -> bool:
    return bool(vapid_public_key() and os.getenv("VAPID_PRIVATE_KEY"))


def sub_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:24]


# ---- 保存 ----

def _collection():
    from firebase_client import get_firestore_client

    return get_firestore_client().collection(COLLECTION)


def _load() -> Dict[str, Any]:
    try:
        with _STORE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _STORE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[news_watch] 保存に失敗しました: {e}")


def get(sid: str) -> Optional[Dict[str, Any]]:
    if use_firestore():
        snap = _collection().document(sid).get()
        return snap.to_dict() if snap.exists else None
    return _load().get(sid)


def _put(sub: Dict[str, Any]) -> None:
    if use_firestore():
        _collection().document(sub["sub_id"]).set(sub)
        return
    with _LOCK:
        data = _load()
        data[sub["sub_id"]] = sub
        _save(data)


def list_subscriptions() -> List[Dict[str, Any]]:
    if use_firestore():
        return [d.to_dict() for d in _collection().stream()]
    return list(_load().values())


def subscribe(subscription: Dict[str, Any], region: str) -> Dict[str, Any]:
    """購読を保存する（同じendpointなら地域だけ更新し、通知履歴は引き継ぐ）"""
    endpoint = str(subscription.get("endpoint") or "")
    keys = subscription.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("購読情報が正しくありません")
    sid = sub_id(endpoint)
    existing = get(sid) or {}
    sub = {
        "sub_id": sid,
        "endpoint": endpoint,
        "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
        "region": region.strip(),
        "created_at": existing.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "notified_ids": existing.get("notified_ids", []),
        "day": existing.get("day", ""),
        "day_count": existing.get("day_count", 0),
    }
    _put(sub)
    return sub


def unsubscribe(endpoint: str) -> bool:
    sid = sub_id(endpoint)
    if use_firestore():
        ref = _collection().document(sid)
        existed = ref.get().exists
        ref.delete()
        return existed
    with _LOCK:
        data = _load()
        existed = data.pop(sid, None) is not None
        _save(data)
    return existed


# ---- 通知のルール ----

def in_quiet_hours(now: datetime) -> bool:
    h = now.astimezone(JST).hour
    return h >= QUIET_START_HOUR or h < QUIET_END_HOUR


def can_notify(sub: Dict[str, Any], news_id: str, now: datetime) -> bool:
    """夜間でなく、1日の上限未満で、同じニュースをまだ通知していないときだけ通知してよい"""
    if in_quiet_hours(now):
        return False
    if news_id in (sub.get("notified_ids") or []):
        return False
    today = now.astimezone(JST).strftime("%Y-%m-%d")
    count = sub.get("day_count", 0) if sub.get("day") == today else 0
    return count < daily_cap()


def mark_notified(sub: Dict[str, Any], news_id: str, now: datetime) -> Dict[str, Any]:
    today = now.astimezone(JST).strftime("%Y-%m-%d")
    count = sub.get("day_count", 0) if sub.get("day") == today else 0
    sub = {
        **sub,
        "day": today,
        "day_count": count + 1,
        "notified_ids": ((sub.get("notified_ids") or []) + [news_id])[-NOTIFIED_KEEP:],
    }
    _put(sub)
    return sub


def build_payload(title: str, link: str, label: str, remark: str, anger_level: int) -> Dict[str, Any]:
    """通知の中身。AIが生成した見解であることを明記する"""
    short = str(label or "").replace("目標", "SDG", 1).strip()
    remark = str(remark or "").strip()
    if len(remark) > REMARK_MAX:
        remark = remark[: REMARK_MAX - 1] + "…"
    return {
        "title": f"🤖 AIの{short}エージェントが注目しています",
        "body": f"{title}\n{remark}{DISCLAIMER}",
        "title_raw": title,
        "link": link,
        "anger_level": anger_level,
    }


def send(sub: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Web Pushを送る。戻り値: "sent" / "gone"（購読が失効。削除済み）/ "error" """
    from pywebpush import webpush, WebPushException

    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=os.getenv("VAPID_PRIVATE_KEY", ""),
            vapid_claims={"sub": os.getenv("VAPID_SUBJECT") or "mailto:civic-lens@example.com"},
            ttl=60 * 60 * 6,
        )
        return "sent"
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            unsubscribe(sub["endpoint"])
            return "gone"
        print(f"[news_watch] 通知の送信に失敗: {e}")
        return "error"
    except Exception as e:
        print(f"[news_watch] 通知の送信に失敗: {e}")
        return "error"
