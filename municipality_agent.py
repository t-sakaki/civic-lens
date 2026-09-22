"""Civic Lens — 自治体特定エージェント

data/authorities/*.json に未収録の自治体がGeolocationで検出された場合に、
情報公開制度を調査してmunicipality_pool（Firestore）に保存する。

調査自体はリクエスト内で同期的に実行する（FastAPIのBackgroundTasksは、Vercel等の
サーバーレス環境ではレスポンス送信後に実行が保証されない＝ユーザーが対象機関
プルダウンにいつまでも反映されない不具合の原因になったため採用しない）。
gmi_client側はGMI_API_KEY未設定時は即座にモックを返すため、実運用でもレスポンスを
大きく遅延させない。
"""
from typing import Dict, Optional

from gmi_client import research_municipality_disclosure_system
from municipality_pool import mark_researching, save_research_result, mark_failed


def research_municipality_now(
    muni_code: str,
    prefecture: str,
    municipality: str,
    full_name: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Dict:
    """自治体の情報公開制度を同期的に調査し、可能な範囲でプールにも保存して結果を返す

    Firestoreが未設定・接続失敗の場合でも、調査結果自体（gmi_client呼び出し）は
    Firestoreに依存しないため、その場で使えるレコードを組み立てて返す。
    """
    research = research_municipality_disclosure_system(municipality, prefecture)

    try:
        mark_researching(muni_code, prefecture, municipality, full_name, lat, lon)
        return save_research_result(muni_code, research)
    except Exception as e:
        print(f"municipality_pool 保存エラー（Firestore未設定の可能性、調査結果はその場で返す）: {e}")
        return {
            "muni_code": muni_code,
            "prefecture": prefecture,
            "municipality": municipality,
            "full_name": full_name,
            "lat": lat,
            "lon": lon,
            "status": "ready",
            "ordinance_name": research.get("ordinance_name"),
            "authority_type": research.get("authority_type"),
            "request_deadline_days": research.get("request_deadline_days", 30),
            "extension_days": research.get("extension_days", 30),
            "review_period_days": research.get("review_period_days", 90),
            "non_disclosure_grounds": research.get("non_disclosure_grounds", []),
            "review_authority": research.get("review_authority"),
            "contact": research.get("contact"),
            "is_mock": research.get("is_mock", False),
        }
