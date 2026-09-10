"""Civic Lens — 現在地から自治体を特定

ブラウザの Geolocation API から取得した緯度経度を、OpenStreetMap Nominatim の
リバースジオコーディングAPI（認証キー不要・無料、利用ポリシー: 1リクエスト/秒程度、
User-Agent必須）で行政区画（都道府県・市区町村）に変換する。

注意: 国土地理院のリバースジオコーダーAPIは町字（大字）名までしか返さず
市区町村名そのものは含まれないため、Nominatimのaddress.city/town/village等を用いる。
"""
import hashlib
import requests
from typing import Optional
from pydantic import BaseModel

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "civic-lens-hackathon/1.0 (municipality geolocation lookup)"


class MunicipalityLocation(BaseModel):
    """現在地から特定した行政区画"""
    muni_code: str          # 都道府県+市区町村名から生成した安定な内部キー（JISコードではない）
    prefecture: str         # "愛知県"
    municipality: str       # "安城市"
    full_name: str          # "愛知県安城市"
    lat: float
    lon: float
    is_mock: bool = False


def _muni_code(prefecture: str, municipality: str) -> str:
    """都道府県+市区町村名から、プール/履歴のキーとして使う安定なハッシュ値を生成する"""
    return hashlib.sha1(f"{prefecture}{municipality}".encode("utf-8")).hexdigest()[:12]


def _mock_location(lat: float, lon: float) -> MunicipalityLocation:
    """API未到達時のフォールバック（安城市をデフォルトとする）"""
    return MunicipalityLocation(
        muni_code=_muni_code("愛知県", "安城市"),
        prefecture="愛知県",
        municipality="安城市",
        full_name="愛知県安城市",
        lat=lat,
        lon=lon,
        is_mock=True,
    )


def detect_municipality(lat: float, lon: float) -> Optional[MunicipalityLocation]:
    """緯度経度から市区町村を特定する（Nominatimリバースジオコーダー使用）"""
    try:
        response = requests.get(
            NOMINATIM_REVERSE_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "accept-language": "ja",
                "zoom": 14,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        address = data.get("address", {})

        prefecture = address.get("province") or address.get("state") or ""
        municipality = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("county")
            or address.get("municipality")
            or ""
        )

        if not prefecture or not municipality:
            return None

        return MunicipalityLocation(
            muni_code=_muni_code(prefecture, municipality),
            prefecture=prefecture,
            municipality=municipality,
            full_name=f"{prefecture}{municipality}",
            lat=lat,
            lon=lon,
            is_mock=False,
        )
    except Exception as e:
        print(f"Nominatim リバースジオコーダー エラー: {e}")
        return _mock_location(lat, lon)
