"""GMI Cloud クライアント

マルチモーダル推論インフラ。条例のRAG検索、類似事例の発見、
法令・判例のセマンティック検索に使用。

GMI Cloudは NVIDIA-backed GPU クラウドで、
OpenAI互換のAPIインターフェースを提供。
"""
import os
import json
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
    is_mock: bool = False  # True の場合、GMI_API_KEY未設定/API失敗によるサンプルデータ


class PrecedentMatch(BaseModel):
    """判例・開示例マッチング結果"""
    title: str
    source: str  # "最高裁" / "高裁" / "他自治体開示事例" 等
    date: str
    relevance_score: float
    summary: str
    url: Optional[str] = None
    is_mock: bool = False  # True の場合、GMI_API_KEY未設定/API失敗によるサンプルデータ


_ORDINANCE_SYSTEM_PROMPT = """あなたは情報公開条例のRAG検索システムです。
クエリに関連する条例条文を検索し、必ず次のJSON形式のみで回答してください（前置き・説明文は一切不要）:

{"results": [{"ordinance_id": "string", "authority": "string", "relevance_score": 0.0〜1.0, "matched_articles": ["string"], "summary": "string"}]}
"""

_PRECEDENT_SYSTEM_PROMPT = """あなたは情報公開・行政事件判例の検索システムです。
クエリに関連する判例・開示事例を検索し、必ず次のJSON形式のみで回答してください（前置き・説明文は一切不要）:

{"results": [{"title": "string", "source": "string", "date": "YYYY-MM-DD", "relevance_score": 0.0〜1.0, "summary": "string", "url": "string または null"}]}
"""


def _call_gmi(system_prompt: str, query: str) -> str:
    """GMI Cloud (OpenAI互換 Chat Completions) を呼び出し、応答テキストを返す"""
    response = requests.post(
        f"{GMI_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {GMI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            "temperature": 0.0,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _extract_json(content: str) -> dict:
    """```json ... ``` 等のコードフェンスを剥がしてJSONとしてパースする"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    return json.loads(text.strip())


def search_ordinances(query: str, top_k: int = 3) -> List[OrdinanceMatch]:
    """条例RAG検索（GMI_API_KEY未設定・API失敗時はサンプルデータに is_mock=True でフォールバック）"""
    if not GMI_API_KEY:
        return _mock_ordinance_search(query, top_k)

    try:
        content = _call_gmi(_ORDINANCE_SYSTEM_PROMPT, query)
        parsed = _extract_json(content)
        results = [OrdinanceMatch(**item, is_mock=False) for item in parsed["results"][:top_k]]
        return results if results else _mock_ordinance_search(query, top_k)
    except Exception as e:
        print(f"GMI Cloud エラー（条例検索）: {e}")
        return _mock_ordinance_search(query, top_k)


def search_precedents(query: str, top_k: int = 5) -> List[PrecedentMatch]:
    """判例・開示例のセマンティック検索（GMI_API_KEY未設定・API失敗時はサンプルデータに is_mock=True でフォールバック）"""
    if not GMI_API_KEY:
        return _mock_precedent_search(query, top_k)

    try:
        content = _call_gmi(_PRECEDENT_SYSTEM_PROMPT, query)
        parsed = _extract_json(content)
        results = [PrecedentMatch(**item, is_mock=False) for item in parsed["results"][:top_k]]
        return results if results else _mock_precedent_search(query, top_k)
    except Exception as e:
        print(f"GMI Cloud エラー（判例検索）: {e}")
        return _mock_precedent_search(query, top_k)


_MUNICIPALITY_RESEARCH_SYSTEM_PROMPT = """あなたは日本の自治体の情報公開制度に関する調査エージェントです。
指定された自治体の情報公開条例について、必ず次のJSON形式のみで回答してください（前置き・説明文は一切不要）:

{"ordinance_name": "string（例: ○○市情報公開条例）", "authority_type": "string（例: 市長）",
 "request_deadline_days": 整数（開示決定までの日数、不明なら30）,
 "extension_days": 整数（延長可能日数、不明なら30）,
 "review_period_days": 整数（審査請求可能日数、不明なら90）,
 "non_disclosure_grounds": [{"number": "string", "name": "string", "description": "string", "exception": "string"}],
 "review_authority": "string（情報公開審査会等）",
 "contact": "string（担当部署）"}

非公式な推測であっても、一般的な自治体情報公開条例の標準的構成（個人情報・法人情報・
審議検討情報・事務事業情報の4類型を含む）に基づいて回答してください。
"""


def research_municipality_disclosure_system(
    municipality: str, prefecture: str = ""
) -> Dict:
    """未収録の自治体について、情報公開条例の概要をLLMで調査する

    バックグラウンドの自治体特定エージェントから呼び出される。GMI_API_KEY未設定・
    API失敗時は一般的な自治体条例のひな形（is_mock=True）を返す。
    """
    query = f"{prefecture}{municipality} の情報公開条例について教えてください。"

    if not GMI_API_KEY:
        return _mock_municipality_research(municipality)

    try:
        content = _call_gmi(_MUNICIPALITY_RESEARCH_SYSTEM_PROMPT, query)
        parsed = _extract_json(content)
        parsed["is_mock"] = False
        return parsed
    except Exception as e:
        print(f"GMI Cloud エラー（自治体情報公開制度調査）: {e}")
        return _mock_municipality_research(municipality)


def _mock_municipality_research(municipality: str) -> Dict:
    """GMI_API_KEY未設定・API失敗時の一般的な条例ひな形"""
    return {
        "ordinance_name": f"{municipality}情報公開条例",
        "authority_type": "市長" if municipality.endswith(("市", "区")) else "町長・村長",
        "request_deadline_days": 30,
        "extension_days": 30,
        "review_period_days": 90,
        "non_disclosure_grounds": [
            {
                "number": "第1号",
                "name": "個人情報",
                "description": "個人に関する情報で、特定の個人を識別することができるもの",
                "exception": "人の生命、健康、生活又は財産を保護するため、公にすることが必要であると認められる情報は開示",
            },
            {
                "number": "第2号",
                "name": "法人情報",
                "description": "法人その他の団体に関する情報であって、公にすることにより当該法人等の正当な利益を害するおそれがあるもの",
                "exception": "",
            },
            {
                "number": "第3号",
                "name": "事務執行影響",
                "description": "審議、検討又は協議に関する情報であって、公にすることにより率直な意見交換等が不当に損なわれるおそれがあるもの",
                "exception": "",
            },
            {
                "number": "第4号",
                "name": "事務事業情報",
                "description": "事務又は事業に関する情報であって、公にすることにより当該事務又は事業の適正な遂行に支障を及ぼすおそれがあるもの",
                "exception": "",
            },
        ],
        "review_authority": f"{municipality}情報公開審査会",
        "contact": f"{municipality}役所 総務課 情報公開担当",
        "is_mock": True,
    }


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
    return [m.model_copy(update={"is_mock": True}) for m in mock_results[:top_k]]


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
    return [m.model_copy(update={"is_mock": True}) for m in mock_results[:top_k]]