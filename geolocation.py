"""Civic Lens — 現在地から自治体を特定

ブラウザの Geolocation API から取得した緯度経度を、OpenStreetMap Nominatim の
リバースジオコーディングAPI（認証キー不要・無料、利用ポリシー: 1リクエスト/秒程度、
User-Agent必須）で行政区画（都道府県・市区町村）に変換する。

注意: 国土地理院のリバースジオコーダーAPIは町字（大字）名までしか返さず
市区町村名そのものは含まれないため、Nominatimのaddress.city/town/village等を用いる。
"""
import hashlib
import json
import math
import time
import requests
from pathlib import Path
from typing import Dict, List, Optional
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


def _reverse_geocode(lat: float, lon: float) -> Optional[Dict]:
    """Nominatimで緯度経度を逆ジオコーディングし、都道府県・市区町村名を返す（失敗時None）"""
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
    address = response.json().get("address", {})

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
    return {"prefecture": prefecture, "municipality": municipality}


def detect_municipality(lat: float, lon: float) -> Optional[MunicipalityLocation]:
    """緯度経度から市区町村を特定する（Nominatimリバースジオコーダー使用）"""
    try:
        found = _reverse_geocode(lat, lon)
        if found is None:
            return None
        prefecture, municipality = found["prefecture"], found["municipality"]
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


def _destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> (float, float):
    """球面三角法で、ある地点から指定した方位・距離だけ離れた地点の緯度経度を求める"""
    r = 6371.0
    lat1, lon1, bearing = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    d_r = distance_km / r

    lat2 = math.asin(
        math.sin(lat1) * math.cos(d_r) + math.cos(lat1) * math.sin(d_r) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(d_r) * math.cos(lat1),
        math.cos(d_r) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def find_nearby_municipalities(
    lat: float,
    lon: float,
    exclude_muni_code: Optional[str] = None,
    radius_km: float = 15.0,
    directions: int = 4,
    request_interval_sec: float = 1.1,
) -> List[Dict]:
    """現在地の周辺（東西南北など）を逆ジオコーディングし、周辺市区町村の候補を返す

    Nominatimは1点の逆ジオコーディングしかできないため、現在地から一定距離・
    複数方位にオフセットした地点をサンプリングして周辺自治体を推定する簡易実装。
    Nominatimの利用ポリシー（概ね1req/秒）に配慮し、リクエスト間に間隔を空ける。
    このため呼び出し全体で数秒かかることがあり、現在地そのものの特定
    （/api/municipality/detect）とは別のオンデマンドな低優先度の呼び出しとして
    使うことを想定している（例: バックグラウンドで後から取得し追加表示する）。
    """
    seen_codes = {exclude_muni_code} if exclude_muni_code else set()
    results: List[Dict] = []

    for i in range(directions):
        if i > 0:
            time.sleep(request_interval_sec)
        bearing = (360.0 / directions) * i
        sample_lat, sample_lon = _destination_point(lat, lon, bearing, radius_km)
        try:
            found = _reverse_geocode(sample_lat, sample_lon)
        except Exception as e:
            print(f"Nominatim リバースジオコーダー エラー（周辺探索）: {e}")
            continue
        if found is None:
            continue

        prefecture, municipality = found["prefecture"], found["municipality"]
        muni_code = _muni_code(prefecture, municipality)
        if muni_code in seen_codes:
            continue
        seen_codes.add(muni_code)
        results.append({
            "muni_code": muni_code,
            "prefecture": prefecture,
            "municipality": municipality,
            "full_name": f"{prefecture}{municipality}",
            "distance_km": radius_km,
        })

    return results


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
