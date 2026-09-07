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
        # 駅すぱあとAPI: 経路探索（v1.27 - /search/course は from/to を受け入れ）
        params = {
            "key": EKISPERT_API_KEY,
            "from": f"{current_lat},{current_lon}",
            "to": office_name,
        }
        response = requests.get(
            f"{EKISPERT_BASE_URL}/search/course",
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
    """APIレスポンスをパース（v1.27対応）"""
    try:
        result = data["ResultSet"]["Course"][0]
        route = result["Route"]
        # v1.27: Price は Course レベルに移動
        price_list = result.get("Price", [])
        fare = 0
        for p in price_list:
            if p.get("Type") == "Fare":
                fare = int(p.get("Oneway", 0))
                break
        points = route.get("Point", [])
        return {
            "from": points[0].get("Station", {}).get("Name", "現在地"),
            "to": points[-1].get("Station", {}).get("Name", to_name),
            "duration_minutes": (
                int(route.get("timeOnBoard", 0))
                + int(route.get("timeWalk", 0))
                + int(route.get("timeOther", 0))
            ),
            "transfer_count": int(route.get("transferCount", 0)),
            "fare_yen": fare,
            "summary": " → ".join(
                [line.get("Name", "") for line in route.get("Line", [])]
            ),
            "is_mock": False,
        }
    except (KeyError, IndexError, TypeError) as e:
        return _mock_route(to_name, note=f"駅すぱあとAPIのレスポンスパースエラー: {e}")


def _mock_route(to_name: str, note: str = "駅すぱあとAPIキー未設定のためサンプルデータを表示しています") -> Dict:
    """APIキーがない場合、またはAPI呼び出し失敗時のモック"""
    return {
        "from": "現在地",
        "to": to_name,
        "duration_minutes": 25,
        "transfer_count": 1,
        "fare_yen": 280,
        "summary": f"現在地 → 最寄り駅 → {to_name}",
        "is_mock": True,
        "note": note,
    }


def get_office_info(authority_key: str) -> Dict:
    """対象機関の窓口情報（data/authorities/*.json を単一の情報源として参照）"""
    from ordinance_data import get_office_info as _get_office_info
    return _get_office_info(authority_key)