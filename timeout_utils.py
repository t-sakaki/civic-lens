"""Gemini呼び出し等、外部APIコールにタイムアウトを付与するための小さなユーティリティ。

google-genai の Client.models.generate_content は呼び出し側でタイムアウトを
指定する標準的な手段がないため、別スレッドで実行しふるいにかける。
"""
from __future__ import annotations

import concurrent.futures
from typing import Callable, TypeVar

T = TypeVar("T")

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini-call")


class GeminiCallTimeout(TimeoutError):
    """Gemini呼び出しが指定時間内に完了しなかったことを示す例外"""


def call_with_timeout(fn: Callable[..., T], *args, timeout_s: float = 25.0, **kwargs) -> T:
    future = _EXECUTOR.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        raise GeminiCallTimeout(f"{getattr(fn, '__qualname__', fn)} が{timeout_s}秒以内に完了しませんでした")
