"""Location Agent

GPS（緯度経度）や住所文字列から、ニュース怒り再現エージェント（news_collector_agent.py）
に渡す「対象地域（市区町村名など）」を推定するエージェント。

ユーザーが地域を明示的に登録していない場合の補助手段として、
ブラウザのGeolocation APIで取得した現在地を逆ジオコーディングし、市区町村名を返す。

外部API: Nominatim（OpenStreetMap、APIキー不要）。
利用規約上、User-Agentの明示とリクエスト頻度の抑制が必要なため、
デモ用途を超えた高頻度呼び出しは行わないこと。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "civic-lens-demo/1.0"

# Nominatim(OSM)の利用規約は低頻度リクエストを前提としているため、
# 同一地点（およそ市区町村スケールの誤差範囲）への再問い合わせをキャッシュして呼び出し回数を抑える。
_CACHE_TTL_SECONDS = 60 * 60  # 1時間
_CACHE_COORD_PRECISION = 2  # 小数点2桁 ≒ 約1.1km四方に丸めてキャッシュキー化
_reverse_geocode_cache: dict[tuple[float, float], tuple[float, Optional["RegionGuess"]]] = {}


@dataclass
class RegionGuess:
    region: str            # 市区町村名（例: "名古屋市"）
    prefecture: Optional[str]  # 都道府県名（例: "愛知県"）
    raw_display_name: str


class LocationAgent:
    """位置情報から対象地域（市区町村）を推定するエージェント"""

    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    def region_from_coordinates(self, lat: float, lon: float) -> Optional[RegionGuess]:
        cache_key = (round(lat, _CACHE_COORD_PRECISION), round(lon, _CACHE_COORD_PRECISION))
        cached = _reverse_geocode_cache.get(cache_key)
        if cached is not None:
            cached_at, cached_guess = cached
            if time.time() - cached_at < _CACHE_TTL_SECONDS:
                return cached_guess

        guess = self._fetch_region_from_coordinates(lat, lon)
        _reverse_geocode_cache[cache_key] = (time.time(), guess)
        return guess

    def _fetch_region_from_coordinates(self, lat: float, lon: float) -> Optional[RegionGuess]:
        try:
            resp = requests.get(
                NOMINATIM_REVERSE_URL,
                params={
                    "format": "jsonv2",
                    "lat": lat,
                    "lon": lon,
                    "accept-language": "ja",
                    "zoom": 12,  # 市区町村レベル
                },
                headers={"User-Agent": USER_AGENT},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[LocationAgent] 逆ジオコーディングに失敗: {e}")
            return None

        address = data.get("address", {})
        # 市区町村に相当するフィールドを優先順に探索（政令市の区・郡部等の揺れを吸収）
        region = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("county")
            or address.get("municipality")
        )
        if not region:
            return None

        return RegionGuess(
            region=region,
            prefecture=address.get("province") or address.get("state"),
            raw_display_name=data.get("display_name", ""),
        )


def get_location_agent() -> LocationAgent:
    return LocationAgent()
