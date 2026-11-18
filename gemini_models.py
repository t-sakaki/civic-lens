"""Geminiのモデル名の一元管理と、モデルが使えなくなったときの自動フォールバック。

フロンティア推論（gemini-4-argon）を最優先しつつ、レート制限・権限エラー・課金枠超過・
モデル廃止などの障害やコスト制御に応じて、より安価／無料枠のあるモデル
（gemini-4-hydrogen → gemini-3.8-flash → gemini-3.5-flash-lite → gemini-flash-lite-latest）
へ自動的に切り替える。

ティア:
  pro      : 深い法務推論・最終書面生成・添付資料読取・統合提案（Argon優先 → 無料枠/安価モデルへ段階降格）
  balanced : 複数エージェント並列生成など品質と速度・費用の両立（Hydrogen優先 → Flash系へ降格）
  flash    : 軽量な判定・分類・スコアリング（Hydrogen(low thinking)優先 → Flash系へ降格）
  free     : 無料枠または最安モデルのみを使う安全ティア

環境変数（カンマ区切り。先頭ほど優先）:
  GEMINI_MODELS_PRO      既定: gemini-4-argon,gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest
  GEMINI_MODELS_BALANCED 既定: gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest
  GEMINI_MODELS_FLASH    既定: gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest
  GEMINI_MODELS_FREE     既定: gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest

思考レベル（Gemini 4 / 3.x の thinking_level 制御）:
  GEMINI_THINKING_PRO / GEMINI_THINKING_BALANCED / GEMINI_THINKING_FLASH / GEMINI_THINKING_FREE
  既定: pro=MEDIUM, balanced=MEDIUM, flash=LOW, free=LOW

安価／無料モデルへの自動降格スイッチ:
  GEMINI_LOW_COST_MODE=1       有料専用モデル（Argon/Carbon/Pro等）をスキップし、無料枠/安価モデルのみ使う
  GEMINI_SOFT_DAILY_LIMIT=N    1日の呼び出し回数がN回を超えたら有料専用モデルを外して安価/無料モデルへ自動降格

使えなくなったモデルの扱い:
  404/403/402、または429でも無料枠制限（limit: 0 等）→ DEAD_TTL_S 秒のあいだスキップ
  429（一時的なレート制限）                             → RATE_LIMIT_COOLDOWN_S 秒のあいだスキップ
  タイムアウト等の一過性エラー                          → 飛ばさず次のモデルへ進むだけ
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
    "pro": "gemini-4-argon,gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest",
    "balanced": "gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest",
    "flash": "gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest",
    "free": "gemini-4-hydrogen,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest",
}

# 無料枠が存在しない（有料ティア専用）のモデル。低コストモードやソフト上限超過時はスキップする
PAID_ONLY_MODELS: frozenset[str] = frozenset({
    "gemini-4-argon",
    "gemini-4-carbon",
    "gemini-3.1-pro",
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-pro-latest",
})

CHEAP_FALLBACK_CHAIN: tuple[str, ...] = (
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
)

DEFAULT_THINKING_LEVELS: dict[str, str] = {
    "pro": "MEDIUM",
    "balanced": "MEDIUM",
    "flash": "LOW",
    "free": "LOW",
}

DEAD_TTL_S = 600.0
RATE_LIMIT_COOLDOWN_S = 60.0
PRO_TIMEOUT_S = 45.0
BALANCED_TIMEOUT_S = 30.0
LIGHT_TIMEOUT_S = 20.0

_skip_until: dict[str, float] = {}
_lock = threading.Lock()


def model_chain(tier: str = "pro") -> list[str]:
    """ティアのモデル優先順リスト（環境変数があればそれを優先）"""
    raw = os.getenv(f"GEMINI_MODELS_{tier.upper()}") or DEFAULT_CHAINS.get(tier) or DEFAULT_CHAINS["pro"]
    return [m.strip() for m in raw.split(",") if m.strip()]


def _is_low_cost_mode(already_consumed: bool = False) -> bool:
    """有料専用モデルを避けて安価・無料モデルを使うべき状態かを返す"""
    flag = os.getenv("GEMINI_LOW_COST_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    soft_raw = os.getenv("GEMINI_SOFT_DAILY_LIMIT", "").strip()
    if soft_raw:
        try:
            soft_limit = int(soft_raw)
        except ValueError:
            soft_limit = 0
        if soft_limit > 0:
            used = cost_guard.usage("gemini")["count"]
            if (used > soft_limit) if already_consumed else (used >= soft_limit):
                return True
    return False


def _is_paid_only(model: str) -> bool:
    m = model.lower()
    if m in PAID_ONLY_MODELS:
        return True
    return "argon" in m or "carbon" in m or "-pro" in m


def _default_timeout(model: str, requested: Optional[float], tier: str = "pro") -> float:
    m = model.lower()
    if "argon" in m or "carbon" in m or "pro" in m:
        return requested if requested is not None else PRO_TIMEOUT_S
    if "hydrogen" in m or tier == "balanced":
        if tier == "flash" or tier == "free":
            return min(requested, LIGHT_TIMEOUT_S) if requested is not None else LIGHT_TIMEOUT_S
        target = BALANCED_TIMEOUT_S
        return min(requested, target) if requested is not None else target
    # 軽量モデル（3.8-flash / flash-lite等）は待たせすぎない（本命が失敗した後の保険のため）
    return min(requested, LIGHT_TIMEOUT_S) if requested is not None else LIGHT_TIMEOUT_S


def _supports_thinking_level(model: str) -> bool:
    """thinking_level パラメータを受け付けるモデルか（flash-lite や旧2.x系は除外）"""
    m = model.lower()
    if "flash-lite" in m:
        return False
    return m.startswith("gemini-4-") or m.startswith("gemini-3.") or m.startswith("gemini-3-")


def config_for_tier(tier: str, model: str, base_config: Any = None) -> Any:
    """ティアとモデルに応じた thinking_level を付与した config を返す（元のオブジェクトは変更しない）"""
    if base_config is not None and getattr(base_config, "thinking_config", None) is not None:
        return base_config
    if not _supports_thinking_level(model):
        return base_config
    level = (os.getenv(f"GEMINI_THINKING_{tier.upper()}") or DEFAULT_THINKING_LEVELS.get(tier) or "").strip().upper()
    if level not in ("MINIMAL", "LOW", "MEDIUM", "HIGH"):
        return base_config
    try:
        from google.genai import types  # type: ignore
    except ImportError:
        return base_config
    thinking_cfg = types.ThinkingConfig(thinking_level=level)
    if base_config is None:
        return types.GenerateContentConfig(thinking_config=thinking_cfg)
    if hasattr(base_config, "model_copy"):
        return base_config.model_copy(update={"thinking_config": thinking_cfg})
    return base_config


def attempts(
    tier: str = "pro",
    timeout_s: Optional[float] = None,
    already_consumed: bool = False,
) -> Iterator[tuple[str, float]]:
    """(モデル名, タイムアウト秒) を優先順に返す。一時的に使えないモデルは飛ばす。

    低コストモードまたはソフト上限超過時は有料専用モデルを外して安価・無料モデルを優先する。
    すべて飛ばされる場合でも全滅させないよう、その場合は全モデルを返す。
    """
    chain = model_chain(tier)
    if _is_low_cost_mode(already_consumed=already_consumed):
        cheap = [m for m in chain if not _is_paid_only(m)]
        chain = cheap or list(CHEAP_FALLBACK_CHAIN)
    now = time.monotonic()
    with _lock:
        usable = [m for m in chain if _skip_until.get(m, 0.0) <= now]
    for model in usable or chain:
        yield model, _default_timeout(model, timeout_s, tier=tier)


def primary_model(tier: str = "pro") -> str:
    """ADKのLlmAgentなど、単一モデル名しか渡せない箇所向け。今使える先頭のモデルを返す。"""
    return next(iter(attempts(tier)))[0]


def _status_code(exc: Exception) -> Optional[int]:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    text = str(exc)
    for marker, value in (
        ("404", 404), ("403", 403), ("402", 402), ("429", 429), ("400", 400),
        ("NOT_FOUND", 404), ("PERMISSION_DENIED", 403),
        ("RESOURCE_EXHAUSTED", 429), ("INVALID_ARGUMENT", 400),
    ):
        if marker in text:
            return value
    return None


def _is_zero_quota_error(exc: Exception) -> bool:
    """無料枠で有料モデルを呼んだとき等の「恒久的なクォータ0」エラーか"""
    text = str(exc).lower()
    return any(marker in text for marker in (
        "limit: 0",
        "free_tier",
        "free tier",
        "billing",
        "paid tier",
    ))


def _is_thinking_unsupported_error(exc: Exception) -> bool:
    code = _status_code(exc)
    if code != 400:
        return False
    text = str(exc).lower()
    return "thinking" in text


def report_failure(model: str, exc: Exception) -> None:
    """モデル呼び出しの失敗を記録し、恒久的/継続的に使えないモデルを一時的に飛ばす"""
    code = _status_code(exc)
    if code in (402, 403, 404):
        ttl = DEAD_TTL_S
    elif code == 429:
        ttl = DEAD_TTL_S if _is_zero_quota_error(exc) else RATE_LIMIT_COOLDOWN_S
    else:
        return
    with _lock:
        _skip_until[model] = time.monotonic() + ttl
    print(f"[gemini_models] {model} を{int(ttl)}秒間スキップします（HTTP {code}）")


def reset_state() -> None:
    """スキップ状態を初期化する（テスト用）"""
    with _lock:
        _skip_until.clear()


def call_model(client: Any, tier: str, model: str, timeout_s: float, contents: Any, config: Any = None):
    """単一モデルをティア別の thinking_level 付きで呼び出す（thinking 非対応の 400 のみ外して再試行）"""
    effective_config = config_for_tier(tier, model, config)
    try:
        kwargs: dict[str, Any] = {"model": model, "contents": contents}
        if effective_config is not None:
            kwargs["config"] = effective_config
        return call_with_timeout(client.models.generate_content, timeout_s=timeout_s, **kwargs)
    except Exception as e:  # noqa: BLE001
        if effective_config is not config and _is_thinking_unsupported_error(e):
            retry_kwargs: dict[str, Any] = {"model": model, "contents": contents}
            if config is not None:
                retry_kwargs["config"] = config
            return call_with_timeout(client.models.generate_content, timeout_s=timeout_s, **retry_kwargs)
        raise


def generate(client: Any, tier: str, contents: Any, config: Any = None, timeout_s: Optional[float] = None):
    """ティアのモデルを優先順に試して generate_content の結果を返す。

    全モデルが失敗した場合は最後の例外を送出する。
    """
    # 日次上限（GEMINI_DAILY_CALL_LIMIT）。超過時は例外を送出し、呼び出し側のルールベース処理に任せる
    cost_guard.consume("gemini")
    last_exc: Optional[Exception] = None
    for model, timeout in attempts(tier, timeout_s, already_consumed=True):
        try:
            return call_model(client, tier, model, timeout, contents, config=config)
        except Exception as e:  # noqa: BLE001 - どのエラーでも次のモデルへ
            last_exc = e
            report_failure(model, e)
            print(f"[gemini_models] {model} で失敗、次のモデルを試します: {str(e)[:200]}")
    assert last_exc is not None
    raise last_exc
