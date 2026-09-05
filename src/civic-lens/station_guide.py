"""駅すぱあとAPIクライアント

市民の市役所アクセスを支援するため、最寄り駅・経路案内を提供。
"""
import os
import requests
from typing import Optional, Dict, List
from pydantic import BaseModel


EKISPERT_API_KEY = os.getenv("EKISPERT_API_KEY", "")
EKISPERT_BASE_URL = "https://api.ekispert.jp/v1/json"


class RouteInfo(BaseModel):
    """経路情報"""
    from_station: str
    to_station: str
    duration_minutes: int
    transfer_count: int
    fare_yen: int
    route_summary: str


def find_nearest_government_office(
    current_lat: float,
    current_lon: float,
    office_name: str = "市役所",
) -> Optional[Dict]:
    """現在地から最寄り駅・市役所までの経路を取得"""
    if not EKISPERT_API_KEY:
        return _mock_route(office_name)

    try:
        # 駅すぱあとAPI: 経路探索
        params = {
            "key": EKISPERT_API_KEY,
            "from": f"{current_lat},{current_lon}",
            "to": office_name,
            "plane": "false",
            "shinkansen": "false",
            "limitedExpress": "false",
            "bus": "false",
            "ferry": "false",
        }
        response = requests.get(
            f"{EKISPERT_BASE_URL}/search/course/extreme",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return _parse_route(data, office_name)
    except Exception as e:
        print(f"駅すぱあとAPI エラー: {e}")
        return _mock_route(office_name)


def _parse_route(data: Dict, to_name: str) -> Dict:
    """APIレスポンスをパース"""
    try:
        result = data["ResultSet"]["Course"][0]
        route = result["Route"]
        return {
            "from": route["Line"][0]["StartStation"]["Name"],
            "to": route["Line"][-1]["EndStation"]["Name"],
            "duration_minutes": route.get("TimeOnBoard", 0)
            + route.get("TimeWalk", 0)
            + route.get("TimeOther", 0),
            "transfer_count": route.get("TransferCount", 0),
            "fare_yen": route.get("Fare", {}).get("Oneway", 0),
            "summary": " → ".join(
                [line["Name"] for line in route.get("Line", [])]
            ),
        }
    except (KeyError, IndexError):
        return _mock_route(to_name)


def _mock_route(to_name: str) -> Dict:
    """APIキーがない場合のモック"""
    return {
        "from": "現在地",
        "to": to_name,
        "duration_minutes": 25,
        "transfer_count": 1,
        "fare_yen": 280,
        "summary": f"現在地 → 最寄り駅 → {to_name}",
        "note": "駅すぱあとAPIキー未設定のためモック",
    }


def get_office_info(authority_key: str) -> Dict:
    """対象自治体の窓口情報"""
    offices = {
        "anjo-city": {
            "name": "安城市役所",
            "address": "〒446-8501 愛知県安城市桜町18番23号",
            "lat": 34.9587,
            "lon": 137.0809,
            "nearest_station": "新安城駅（名鉄西尾線）",
        },
        "nagoya-city": {
            "name": "名古屋市役所",
            "address": "〒460-8508 名古屋市中区三の丸三丁目1番2号",
            "lat": 35.1815,
            "lon": 136.9066,
            "nearest_station": "市役所駅（名古屋市営地下鉄名城線）",
        },
        "okazaki-city": {
            "name": "岡崎市役所",
            "address": "〒444-8601 愛知県岡崎市十王町2丁目9番地",
            "lat": 34.9554,
            "lon": 137.1737,
            "nearest_station": "東岡崎駅（名鉄名古屋本線）",
        },
        "aichi-pref": {
            "name": "愛知県庁",
            "address": "〒460-8501 名古屋市中区三の丸三丁目1番2号",
            "lat": 35.1803,
            "lon": 136.9067,
            "nearest_station": "市役所駅（名古屋市営地下鉄名城線）",
        },
        "aichi-assembly": {
            "name": "愛知県議会",
            "address": "〒460-8501 名古屋市中区三の丸三丁目1番2号",
            "lat": 35.1803,
            "lon": 136.9067,
            "nearest_station": "市役所駅（名古屋市営地下鉄名城線）",
        },
    }
    return offices.get(authority_key, offices["anjo-city"])