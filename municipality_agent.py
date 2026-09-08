"""Civic Lens — 自治体特定バックグラウンドエージェント

data/authorities/*.json に未収録の自治体がGeolocationで検出された場合に、
リクエストをブロックせずバックグラウンドで情報公開制度を調査し、
municipality_pool（Firestore）に結果を保存する。
"""
from gmi_client import research_municipality_disclosure_system
from municipality_pool import mark_researching, save_research_result, mark_failed


def research_and_pool_municipality(muni_code: str, prefecture: str, municipality: str, full_name: str) -> None:
    """バックグラウンドタスクとして実行: 自治体の情報公開条例を調査しプールに保存する

    Firestore未設定・接続失敗時も例外を外に漏らさない（BackgroundTasks内の例外は
    レスポンスに影響しないが、ログにだけは残す）。
    """
    try:
        mark_researching(muni_code, prefecture, municipality, full_name)
        research = research_municipality_disclosure_system(municipality, prefecture)
        save_research_result(muni_code, research)
    except Exception as e:
        print(f"自治体調査バックグラウンドタスクエラー（{full_name}）: {e}")
        try:
            mark_failed(muni_code, str(e))
        except Exception:
            pass
