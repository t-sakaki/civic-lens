"""請求日（実際に開示請求を提出した日）の検証と変換。

保存データ・IPFS・オンチェーン台帳のすべてで同じ検証を使う。請求日は請求者本人の申告であり、
オンチェーンのブロック時刻（記録した時刻）とは別のもの。台帳では「請求者の申告」として表示し、
期限の目安の起算日に使う。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

JST = timezone(timedelta(hours=9))
EARLIEST = date(2000, 1, 1)


def today_jst() -> date:
    return datetime.now(JST).date()


def parse_request_date(value: Optional[str]) -> Optional[date]:
    """YYYY-MM-DD を検証して date を返す。空なら None。未来の日付・2000年より前は ValueError。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        d = date.fromisoformat(text)
    except ValueError:
        raise ValueError("請求日は YYYY-MM-DD 形式で入力してください。")
    if d > today_jst():
        raise ValueError("請求日に未来の日付は指定できません。")
    if d < EARLIEST:
        raise ValueError("請求日が古すぎます。")
    return d


def to_unix(d: date) -> int:
    """その日の 0 時（JST）の UNIX 秒"""
    return int(datetime(d.year, d.month, d.day, tzinfo=JST).timestamp())


def from_unix(ts: int) -> Optional[date]:
    """UNIX 秒を JST の日付に。範囲外（0 や極端な値）は None。"""
    try:
        d = datetime.fromtimestamp(int(ts), JST).date()
    except (ValueError, OverflowError, OSError):
        return None
    return d if EARLIEST <= d else None
