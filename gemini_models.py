"""Geminiのモデル名の一元管理と、モデルが使えなくなったときの自動フォールバック。

プラン期限切れ・モデル廃止・レート制限で特定モデルが使えなくなっても、ルールベースの定型文に
落ちる前に別のモデルへ切り替えられるようにする。モデル名は環境変数で差し替え可能。

ティア:
  pro   : 品質重視の生成（怒り分析・請求書生成・添付資料の読み取りなど）
  flash : 軽量な判定・分類（ニュースにどのエージェントが登場するか等）。速度とコストを優先

環境変数（カンマ区切り。先頭ほど優先）:
  GEMINI_MODELS_PRO   既定: gemini-pro-latest,gemini-flash-latest,gemini-flash-lite-latest
  GEMINI_MODELS_FLASH 既定: gemini-flash-latest,gemini-flash-lite-latest

使えなくなったモデルの扱い:
  404/403（廃止・権限なし）→ DEAD_TTL_S 秒のあいだ試行を飛ばす（毎回待たされないように）
  429（レート制限）      → RATE_LIMIT_COOLDOWN_S 秒のあいだ飛ばす
  タイムアウト等の一過性エラー → 飛ばさず次のモデルへ進むだけ
全モデルが使えない場合は最後の例外をそのまま送出する（呼び出し側のルールベース処理に任せる）。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Iterator, Optional

from timeout_utils import call_with_timeout
import cost_guard

DEFAULT_CHAINS: dict[str, str] = {
    "pro": "gemini-pro-latest,gemini-flash-latest,gemini-flash-lite-latest",
    "flash": "gemini-flash-latest,gemini-flash-lite-latest",
}
DEAD_TTL_S = 600.0
RATE_LIMIT_COOLDOWN_S = 60.0
PRO_TIMEOUT_S = 40.0
LIGHT_TIMEOUT_S = 20.0

_skip_until: dict[str, float] = {}
_lock = threading.Lock()


def model_chain(tier: str = "pro") -> list[str]:
    """ティアのモデル優先順リスト（環境変数があればそれを優先）"""
    raw = os.getenv(f"GEMINI_MODELS_{tier.upper()}") or DEFAULT_CHAINS.get(tier) or DEFAULT_CHAINS["pro"]
    return [m.strip() for m in raw.split(",") if m.strip()]


def _default_timeout(model: str, requested: Optional[float]) -> float:
    if "pro" in model:
        return requested if requested is not None else PRO_TIMEOUT_S
    # 軽量モデルは待たせすぎない（本命が失敗した後の保険のため）
    return min(requested, LIGHT_TIMEOUT_S) if requested is not None else LIGHT_TIMEOUT_S


def attempts(tier: str = "pro", timeout_s: Optional[float] = None) -> Iterator[tuple[str, float]]:
    """(モデル名, タイムアウト秒) を優先順に返す。一時的に使えないモデルは飛ばす。

    すべて飛ばされる場合でも全滅させないよう、その場合は全モデルを返す。
    """
    chain = model_chain(tier)
    now = time.monotonic()
    with _lock:
        usable = [m for m in chain if _skip_until.get(m, 0.0) <= now]
    for model in usable or chain:
        yield model, _default_timeout(model, timeout_s)


def primary_model(tier: str = "pro") -> str:
    """ADKのLlmAgentなど、単一モデル名しか渡せない箇所向け。今使える先頭のモデルを返す。"""
    return next(iter(attempts(tier)))[0]


def _status_code(exc: Exception) -> Optional[int]:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    text = str(exc)
    for marker, value in (("404", 404), ("403", 403), ("429", 429), ("NOT_FOUND", 404),
                          ("PERMISSION_DENIED", 403), ("RESOURCE_EXHAUSTED", 429)):
        if marker in text:
            return value
    return None


def report_failure(model: str, exc: Exception) -> None:
    """モデル呼び出しの失敗を記録し、恒久的/継続的に使えないモデルを一時的に飛ばす"""
    code = _status_code(exc)
    if code in (403, 404):
        ttl = DEAD_TTL_S
    elif code == 429:
        ttl = RATE_LIMIT_COOLDOWN_S
    else:
        return
    with _lock:
        _skip_until[model] = time.monotonic() + ttl
    print(f"[gemini_models] {model} を{int(ttl)}秒間スキップします（HTTP {code}）")


def reset_state() -> None:
    """スキップ状態を初期化する（テスト用）"""
    with _lock:
        _skip_until.clear()


def generate(client: Any, tier: str, contents: Any, config: Any = None, timeout_s: Optional[float] = None):
    """ティアのモデルを優先順に試して generate_content の結果を返す。

    全モデルが失敗した場合は最後の例外を送出する。
    """
    # 日次上限（GEMINI_DAILY_CALL_LIMIT）。超過時は例外を送出し、呼び出し側のルールベース処理に任せる
    cost_guard.consume("gemini")
    last_exc: Optional[Exception] = None
    for model, timeout in attempts(tier, timeout_s):
        try:
            kwargs: dict[str, Any] = {"model": model, "contents": contents}
            if config is not None:
                kwargs["config"] = config
            return call_with_timeout(client.models.generate_content, timeout_s=timeout, **kwargs)
        except Exception as e:  # noqa: BLE001 - どのエラーでも次のモデルへ
            last_exc = e
            report_failure(model, e)
            print(f"[gemini_models] {model} で失敗、次のモデルを試します: {str(e)[:200]}")
    assert last_exc is not None
    raise last_exc
