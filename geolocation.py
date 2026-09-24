"""Civic Lens — 現在地から自治体を特定

ブラウザの Geolocation API から取得した緯度経度を、OpenStreetMap Nominatim の
リバースジオコーディングAPI（認証キー不要・無料、利用ポリシー: 1リクエスト/秒程度、
User-Agent必須）で行政区画（都道府県・市区町村）に変換する。

注意: 国土地理院のリバースジオコーダーAPIは町字（大字）名までしか返さず
市区町村名そのものは含まれないため、Nominatimのaddress.city/town/village等を用いる。
"""
import hashlib
import json
import requests
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "civic-lens-hackathon/1.0 (municipality geolocation lookup)"
_PREFECTURES_PATH = Path(__file__).resolve().parent / "data" / "prefectures.json"


def list_prefectures() -> List[str]:
    """全47都道府県名の一覧を返す（任意選択UIのプルダウン用マスタ）"""
    with open(_PREFECTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


class MunicipalityLocation(BaseModel):
    """特定した行政区画（現在地から、または手動選択から）"""
    muni_code: str          # 都道府県+市区町村名から生成した安定な内部キー（JISコードではない）
    prefecture: str         # "愛知県"
    municipality: str       # "安城市"
    full_name: str          # "愛知県安城市"
    lat: Optional[float] = None  # 手動選択（GPS不使用）の場合は None
    lon: Optional[float] = None
    is_mock: bool = False


def _muni_code(prefecture: str, municipality: str) -> str:
    """都道府県+市区町村名から、プール/履歴のキーとして使う安定なハッシュ値を生成する"""
    return hashlib.sha1(f"{prefecture}{municipality}".encode("utf-8")).hexdigest()[:12]


def municipality_code(prefecture: str, municipality: str) -> str:
    """_muni_code() の公開ラッパー（GPS無しの手動選択から muni_code を求める用途）"""
    return _muni_code(prefecture, municipality)


def prefecture_code(prefecture: str) -> str:
    """都道府県名から、都道府県レベルのプールキーとして使う安定なハッシュ値を生成する

    "prefecture:" 接頭辞を付けることで、同名の市区町村が万一存在しても
    _muni_code() のキー空間と衝突しないようにしている。
    """
    return hashlib.sha1(f"prefecture:{prefecture}".encode("utf-8")).hexdigest()[:12]


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


def build_manual_location(prefecture: str, municipality: str) -> MunicipalityLocation:
    """GPSを使わず、ユーザーが手動入力した都道府県・市区町村から行政区画を組み立てる

    現在地から特定した場合と同じ MunicipalityLocation を返すことで、
    /api/municipality/detect と後続処理（プール参照・調査・履歴保存）を共有できる。
    lat/lon が無い分、距離ベースの候補列挙（list_nearby_authorities）は使えない。
    """
    return MunicipalityLocation(
        muni_code=_muni_code(prefecture, municipality),
        prefecture=prefecture,
        municipality=municipality,
        full_name=f"{prefecture}{municipality}",
        lat=None,
        lon=None,
        is_mock=False,
    )
