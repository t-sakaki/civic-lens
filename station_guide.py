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
            "is_mock": False,
        }
    except (KeyError, IndexError):
        return _mock_route(to_name, note="駅すぱあとAPIのレスポンス形式が想定と異なるためサンプルデータを表示しています")


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