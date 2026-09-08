"""Civic Lens — 現在地から自治体を特定

ブラウザの Geolocation API から取得した緯度経度を、国土交通省 国土地理院の
リバースジオコーディングAPI（認証キー不要・無料）で行政区画（都道府県・市区町村）
に変換する。

このAPIはJIS都道府県コード（先頭2桁）+ 市区町村コード（後続3桁）から成る
全国地方公共団体コード（muniCd）を返すため、都道府県名は固定テーブルで解決する。
"""
import requests
from typing import Optional
from pydantic import BaseModel

GSI_REVERSE_GEOCODER_URL = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"

PREFECTURE_CODES = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県", "05": "秋田県",
    "06": "山形県", "07": "福島県", "08": "茨城県", "09": "栃木県", "10": "群馬県",
    "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県", "15": "新潟県",
    "16": "富山県", "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県", "25": "滋賀県",
    "26": "京都府", "27": "大阪府", "28": "兵庫県", "29": "奈良県", "30": "和歌山県",
    "31": "鳥取県", "32": "島根県", "33": "岡山県", "34": "広島県", "35": "山口県",
    "36": "徳島県", "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
    "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県", "45": "宮崎県",
    "46": "鹿児島県", "47": "沖縄県",
}


class MunicipalityLocation(BaseModel):
    """現在地から特定した行政区画"""
    muni_code: str          # 全国地方公共団体コード（5桁）
    prefecture: str         # "愛知県"
    municipality: str       # "安城市"
    full_name: str          # "愛知県安城市"
    lat: float
    lon: float
    is_mock: bool = False


def _mock_location(lat: float, lon: float) -> MunicipalityLocation:
    """API未到達時のフォールバック（安城市をデフォルトとする）"""
    return MunicipalityLocation(
        muni_code="23212",
        prefecture="愛知県",
        municipality="安城市",
        full_name="愛知県安城市",
        lat=lat,
        lon=lon,
        is_mock=True,
    )


def detect_municipality(lat: float, lon: float) -> Optional[MunicipalityLocation]:
    """緯度経度から市区町村を特定する（国土地理院リバースジオコーダー使用）"""
    try:
        response = requests.get(
            GSI_REVERSE_GEOCODER_URL,
            params={"lat": lat, "lon": lon},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results")
        if not results:
            return None

        muni_code = str(results.get("muniCd", "")).zfill(5)
        muni_name = results.get("lv01Nm", "")
        pref_code = muni_code[:2]
        pref_name = PREFECTURE_CODES.get(pref_code, "")

        if not muni_code or not muni_name:
            return None

        return MunicipalityLocation(
            muni_code=muni_code,
            prefecture=pref_name,
            municipality=muni_name,
            full_name=f"{pref_name}{muni_name}",
            lat=lat,
            lon=lon,
            is_mock=False,
        )
    except Exception as e:
        print(f"国土地理院リバースジオコーダー エラー: {e}")
        return _mock_location(lat, lon)
