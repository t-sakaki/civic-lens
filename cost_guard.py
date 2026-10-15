"""費用ガード: 公開デモURLで Gemini / TTS の呼び出しが青天井にならないようにする。

Firebase の支出上限は Firebase AI Logic / Cloud Functions / App Hosting が対象で、Cloud Run 上で
Gemini API を直接呼ぶ本アプリには効かない。そのためアプリ側で次の2層を持つ（Cloud Billing の
予算アラートと併用する想定）。

  1. 日次の呼び出し上限（全体）  : 上限に達したらAI呼び出しを止め、ルールベースのフォールバックへ落とす
     - GEMINI_DAILY_CALL_LIMIT  Gemini テキスト生成の1日あたり上限（0 または未設定で無制限）
     - TTS_DAILY_CALL_LIMIT     TTS の1日あたり上限（既定 200）
  2. IPごとのレート制限（高コストAPI）: RATE_LIMIT_PER_MINUTE（既定 20、0で無効）
  3. IPごとの生成系レート制限（分析・開示請求書・審査請求書・SDGs採点。1回で Gemini を複数回呼ぶ）:
     GENERATION_RATE_LIMIT_PER_MINUTE（既定 5）/ GENERATION_RATE_LIMIT_PER_HOUR（既定 30）。0で無効
     超過時は 429 と日本語メッセージ・Retry-After ヘッダを返す

注意: Cloud Run が複数インスタンスにスケールすると、カウンタはインスタンスごとに分かれる
（実効上限は 上限 × インスタンス数。現在は --max-instances 3）。厳密に守るなら Firestore / Redis 等の共有ストアが必要。

カウンタはプロセス内（Cloud Runのインスタンスごと）。厳密な課金制御ではなく、暴走の歯止めである。
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

_JST = timezone(timedelta(hours=9))
_lock = threading.Lock()
_daily: dict[str, tuple[str, int]] = {}  # kind -> (日付, 回数)
_hits: dict[str, deque] = defaultdict(deque)  # ip -> 直近の呼び出し時刻
_gen_hits: dict[str, deque] = defaultdict(deque)  # ip -> 生成系APIの直近1時間の呼び出し時刻


class BudgetExceeded(RuntimeError):
    """日次上限に達した。呼び出し側はルールベースのフォールバックへ進む"""


def _limit(kind: str) -> int:
    default = "200" if kind == "tts" else "0"
    try:
        return int(os.getenv(f"{kind.upper()}_DAILY_CALL_LIMIT") or default)
    except ValueError:
        return int(default)


def consume(kind: str) -> None:
    """kind（"gemini" / "tts"）の呼び出しを1回分数える。上限超過なら BudgetExceeded"""
    limit = _limit(kind)
    today = datetime.now(_JST).strftime("%Y-%m-%d")
    with _lock:
        day, count = _daily.get(kind, (today, 0))
        if day != today:
            count = 0
        if limit > 0 and count >= limit:
            raise BudgetExceeded(f"{kind} の日次上限（{limit}回）に達しました")
        _daily[kind] = (today, count + 1)


def usage(kind: str) -> dict:
    today = datetime.now(_JST).strftime("%Y-%m-%d")
    with _lock:
        day, count = _daily.get(kind, (today, 0))
    return {"date": today, "count": count if day == today else 0, "limit": _limit(kind)}


def _client_ip(request: Request) -> str:
    # Cloud Run では X-Forwarded-For の先頭がクライアント
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


async def rate_limit(request: Request) -> None:
    """FastAPI の Depends 用。高コストAPIにIPごとの1分あたり上限をかける"""
    try:
        per_min = int(os.getenv("RATE_LIMIT_PER_MINUTE") or "20")
    except ValueError:
        per_min = 20
    if per_min <= 0:
        return
    ip = _client_ip(request)
    now = time.monotonic()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_min:
            raise HTTPException(status_code=429, detail="リクエストが多すぎます。少し待ってからもう一度お試しください。")
        q.append(now)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) if os.getenv(name) not in (None, "") else default)
    except ValueError:
        return default


GENERATION_LIMIT_MESSAGE = "AIによる分析・文書作成のご利用が集中しています。{wait}ほど待ってから、もう一度お試しください。"


async def generation_rate_limit(request: Request) -> None:
    """FastAPI の Depends 用。分析・開示請求書・審査請求書・SDGs採点など、Gemini を呼ぶ生成系APIのIPごとの上限"""
    per_min = _env_int("GENERATION_RATE_LIMIT_PER_MINUTE", 5)
    per_hour = _env_int("GENERATION_RATE_LIMIT_PER_HOUR", 30)
    if per_min <= 0 and per_hour <= 0:
        return
    ip = _client_ip(request)
    now = time.monotonic()
    with _lock:
        q = _gen_hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        last_min = [t for t in q if now - t <= 60]
        retry = 0
        if per_min > 0 and len(last_min) >= per_min:
            retry = int(60 - (now - last_min[0])) + 1
        elif per_hour > 0 and len(q) >= per_hour:
            retry = int(3600 - (now - q[0])) + 1
        if retry:
            wait = f"{retry}秒" if retry < 120 else f"{(retry + 59) // 60}分"
            raise HTTPException(status_code=429, detail=GENERATION_LIMIT_MESSAGE.format(wait=wait),
                                headers={"Retry-After": str(retry)})
        q.append(now)


def reset_state() -> None:
    """カウンタを初期化する（テスト用）"""
    with _lock:
        _daily.clear()
        _hits.clear()
        _gen_hits.clear()
