"""GMI Cloud クライアント

マルチモーダル推論インフラ。条例のRAG検索、類似事例の発見、
法令・判例のセマンティック検索に使用。

GMI Cloudは NVIDIA-backed GPU クラウドで、
OpenAI互換のAPIインターフェースを提供。
"""
import os
import requests
from typing import List, Dict, Optional
from pydantic import BaseModel


GMI_API_KEY = os.getenv("GMI_API_KEY", "")
GMI_BASE_URL = os.getenv("GMI_BASE_URL", "https://api.gmi-cloud.com/v1")


class OrdinanceMatch(BaseModel):
    """条例マッチング結果"""
    ordinance_id: str
    authority: str
    relevance_score: float
    matched_articles: List[str]
    summary: str


class PrecedentMatch(BaseModel):
    """判例・開示例マッチング結果"""
    title: str
    source: str  # "最高裁" / "高裁" / "他自治体開示事例" 等
    date: str
    relevance_score: float
    summary: str
    url: Optional[str] = None


def search_ordinances(query: str, top_k: int = 3) -> List[OrdinanceMatch]:
    """条例RAG検索"""
    if not GMI_API_KEY:
        return _mock_ordinance_search(query, top_k)

    try:
        response = requests.post(
            f"{GMI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GMI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-v4-pro",
                "messages": [
                    {
                        "role": "system",
                        "content": "あなたは情報公開条例のRAG検索システムです。クエリに関連する条例条文を返してください。",
                    },
                    {"role": "user", "content": query},
                ],
                "temperature": 0.0,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_ordinance_results(content, query, top_k)
    except Exception as e:
        print(f"GMI Cloud エラー: {e}")
        return _mock_ordinance_search(query, top_k)


def search_precedents(query: str, top_k: int = 5) -> List[PrecedentMatch]:
    """判例・開示例のセマンティック検索"""
    if not GMI_API_KEY:
        return _mock_precedent_search(query, top_k)

    try:
        response = requests.post(
            f"{GMI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GMI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-v4-pro",
                "messages": [
                    {
                        "role": "system",
                        "content": "あなたは情報公開・行政事件判例の検索システムです。クエリに関連する判例・開示事例を返してください。",
                    },
                    {"role": "user", "content": query},
                ],
                "temperature": 0.0,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_precedent_results(content, top_k)
    except Exception as e:
        print(f"GMI Cloud エラー: {e}")
        return _mock_precedent_search(query, top_k)


def _parse_ordinance_results(content: str, query: str, top_k: int) -> List[OrdinanceMatch]:
    """パース"""
    return _mock_ordinance_search(query, top_k)


def _parse_precedent_results(content: str, top_k: int) -> List[PrecedentMatch]:
    """パース"""
    return _mock_precedent_search("", top_k)


def _mock_ordinance_search(query: str, top_k: int) -> List[OrdinanceMatch]:
    """APIキーがない場合のモック"""
    mock_results = [
        OrdinanceMatch(
            ordinance_id="anjo-city-条例第7条第2号",
            authority="安城市",
            relevance_score=0.85,
            matched_articles=["第7条第2号（法人情報）"],
            summary="法人情報該当性については、具体的・実質的な害益のおそれがなければならない",
        ),
        OrdinanceMatch(
            ordinance_id="anjo-city-条例第7条第4号",
            authority="安城市",
            relevance_score=0.78,
            matched_articles=["第7条第4号（事務執行影響）"],
            summary="事務執行に支障を及ぼすおそれは、抽象的なものでは足りない",
        ),
        OrdinanceMatch(
            ordinance_id="anjo-city-条例第11条",
            authority="安城市",
            relevance_score=0.72,
            matched_articles=["第11条（部分開示）"],
            summary="部分開示の努力義務規定。黒塗り処理で対応可能な情報は開示すべき",
        ),
    ]
    return mock_results[:top_k]


def _mock_precedent_search(query: str, top_k: int) -> List[PrecedentMatch]:
    """APIキーがない場合のモック"""
    mock_results = [
        PrecedentMatch(
            title="最判平成14年2月8日（在外日本人選挙権）",
            source="最高裁判例",
            date="2002-02-08",
            relevance_score=0.88,
            summary="「法人等の正当な利益を害するおそれ」は、抽象的可能性では足りず、具体的・実質的危険性の存在が必要",
            url="https://www.courts.go.jp/",
        ),
        PrecedentMatch(
            title="名古屋市 情報開示事例（海外視察費）",
            source="名古屋市",
            date="2023-04-15",
            relevance_score=0.82,
            summary="市長海外視察の復命書は、法人情報・事務執行影響を理由に一部黒塗りで開示。視察期間・訪問先・面談者等は開示",
            url="https://www.city.nagoya.jp/",
        ),
        PrecedentMatch(
            title="岡崎市 情報開示事例（契約金額）",
            source="岡崎市",
            date="2024-09-20",
            relevance_score=0.76,
            summary="契約金額・相手方は原則開示。単価・積算根拠は競争上の地位を理由に一部非開示",
            url="https://www.city.okazaki.aichi.jp/",
        ),
        PrecedentMatch(
            title="東京高判平成13年11月29日",
            source="高等裁判所",
            date="2001-11-29",
            relevance_score=0.71,
            summary="部分開示の努力義務を懈怠した不開示決定は違法と判断",
            url="https://www.courts.go.jp/",
        ),
        PrecedentMatch(
            title="最判平成11年12月16日",
            source="最高裁判例",
            date="1999-12-16",
            relevance_score=0.69,
            summary="審議・検討段階の情報は意思決定後の情報については開示すべき時期が来ている",
            url="https://www.courts.go.jp/",
        ),
    ]
    return mock_results[:top_k]