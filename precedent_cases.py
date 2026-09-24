"""総務省「行政不服審査裁決・答申検索データベース」から収集した認容事例を扱うモジュール。

data/gyofuku_cases.json を読み込み、
1. build_counter_argument() から利用する「関連度の高い認容事例の検索」
2. アプリ内の閲覧・検索ページ（/precedent-cases, /api/precedent-cases）
に使う。

収集スクリプト: scripts/scrape_gyofuku_cases.py
出典: 総務省 行政不服審査裁決・答申検索データベース
      (https://fufukudb.search.soumu.go.jp/koukai/Main)
利用規約: 公共データ利用規約(PDL1.0)。出典明記のうえ、概要スニペットのみ保持し
          全文は保存していない（詳細は data/gyofuku_cases.json の license_note を参照）。

## 検索方式
1. **埋め込みベクトル検索（優先）**: Gemini Embeddings API (gemini-embedding-001) で
   事例テキストとクエリをベクトル化し、コサイン類似度で検索する。
   事前計算済みベクトルは data/gyofuku_cases_embeddings.json（scripts/build_precedent_embeddings.py
   で生成）に保存されており、case_id で本体データと紐付ける。
2. **Ngramヒューリスティック検索（フォールバック）**: GEMINI_API_KEY未設定・
   埋め込みデータ未生成・API呼び出し失敗時に自動的にこちらへフォールバックする。
"""
from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "gyofuku_cases.json"
EMBEDDINGS_PATH = BASE_DIR / "data" / "gyofuku_cases_embeddings.json"

# 埋め込みに使うGeminiモデル。scripts/build_precedent_embeddings.py と揃えること。
EMBEDDING_MODEL = "gemini-embedding-001"

_STOPWORDS = {"の", "に", "は", "を", "が", "で", "と", "第", "条", "項", "号", "等", "及び", "又は"}

# 審査庁名 → 機関種別、処分根拠法令 → 分野タグの分類ルール。
# fufuku-news (https://fufuku-news.pages.dev/, 同一制作者による姉妹サイト) の
# src/lib/classify.js と分類ロジック・タグ体系を揃えている。
_CENTRAL_MINISTRIES = [
    "内閣府", "総務省", "法務省", "外務省", "財務省", "文部科学省", "厚生労働省",
    "農林水産省", "経済産業省", "国土交通省", "環境省", "防衛省", "デジタル庁",
    "復興庁", "こども家庭庁", "国税庁", "特許庁", "消防庁", "公正取引委員会",
    "個人情報保護委員会", "公害等調整委員会",
]

_AGENCY_TYPE_MUNICIPALITY_RE = re.compile(r"(市|区|町|村)(長)?$")
_AGENCY_TYPE_PREFECTURE_RE = re.compile(r"(都|道|府|県)$")

_TOPIC_RULES: list[tuple[str, re.Pattern]] = [
    ("生活保護", re.compile(r"生活保護")),
    ("情報公開", re.compile(r"情報公開|情報の公開に関する法律")),
    ("個人情報保護", re.compile(r"個人情報")),
    ("税務", re.compile(r"地方税法|税条例")),
    ("子ども・子育て", re.compile(
        r"児童福祉法|児童扶養手当|子ども・子育て支援法|ひとり親家庭手当|"
        r"保育所|保育施設|地域型保育事業|母子及び父子並びに寡婦福祉法"
    )),
    ("障害者福祉", re.compile(
        r"障害者の日常生活及び社会生活を総合的に支援|精神保健及び.*障害.*福祉に関する法律|"
        r"身体障害者福祉法|療育手帳"
    )),
    ("介護・高齢者福祉", re.compile(r"介護保険法|要介護高齢者手当")),
    ("災害弔慰金", re.compile(r"災害弔慰金")),
    ("農地", re.compile(r"農地法")),
    ("国民健康保険", re.compile(r"国民健康保険法")),
    ("医療", re.compile(r"医療法")),
    ("特許", re.compile(r"特許法")),
    ("宗教法人", re.compile(r"宗教法人法")),
    ("生活安全・風俗", re.compile(r"客引き行為")),
]


def classify_agency_type(authority: str) -> str:
    """審査庁名（authority）から機関種別を分類する。"""
    if not authority:
        return "その他"
    if "公安委員会" in authority:
        return "公安委員会"
    if any(m in authority for m in _CENTRAL_MINISTRIES):
        return "中央省庁"
    if "審査会" in authority:
        return "審査会等"
    if _AGENCY_TYPE_MUNICIPALITY_RE.search(authority):
        return "市区町村"
    if _AGENCY_TYPE_PREFECTURE_RE.search(authority):
        return "都道府県"
    return "その他"


def classify_topics(basis_laws: str) -> list[str]:
    """処分根拠法令（basis_laws）から分野タグを分類する（複数該当可）。"""
    if not basis_laws:
        return ["その他"]
    matched = [tag for tag, pattern in _TOPIC_RULES if pattern.search(basis_laws)]
    return matched or ["その他"]


def build_case_title(authority: str, result: str, basis_laws: str) -> str:
    """一覧表示用の見出しを組み立てる（fufuku-newsのbuildHeadline()と同じ書式）。

    scripts/scrape_gyofuku_cases.py（収集時）とこの関数（表示時の後方互換フォールバック）
    の双方から使う。
    """
    law_label = (basis_laws or "").strip() or "行政処分"
    return f"【{result or '認容'}】{authority or '不明'}：{law_label}をめぐる審査請求"


def _enrich_case(case: dict) -> dict:
    """一覧・検索APIで返す事例にtitle/agency_type/tags/impact_commentを付与する。

    title・impact_commentは新スキーマ（scrape_gyofuku_cases.py側で収集時に設定）を優先し、
    旧スキーマのレコード（収集時点でtitleが未設定）にはここでフォールバックを生成する。
    """
    return {
        **case,
        "title": case.get("title") or build_case_title(
            case.get("authority", ""), case.get("result", ""), case.get("basis_laws", "")
        ),
        "agency_type": classify_agency_type(case.get("authority", "")),
        "tags": classify_topics(case.get("basis_laws", "")),
        "impact_comment": case.get("impact_comment", ""),
    }


def _tokenize(text: str) -> set[str]:
    """ごく簡易な日本語トークナイズ（2〜4文字のNgram + 既知キーワード抽出）。"""
    if not text:
        return set()
    text = re.sub(r"[\s　]+", "", text)
    tokens: set[str] = set()
    # 2文字Ngramは日本語では偶然一致しやすく誤マッチの原因になるため、
    # ある程度の特異性を持つ3〜4文字Ngramのみを使う。
    for n in (3, 4):
        for i in range(len(text) - n + 1):
            tokens.add(text[i:i + n])
    return tokens


@lru_cache(maxsize=1)
def _load_cases() -> list[dict]:
    if not DATA_PATH.exists():
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("cases", [])


@lru_cache(maxsize=1)
def _load_embeddings() -> dict[str, list[float]]:
    """data/gyofuku_cases_embeddings.json を読み込む。case_id -> ベクトル。"""
    if not EMBEDDINGS_PATH.exists():
        return {}
    try:
        with open(EMBEDDINGS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    embeddings = payload.get("embeddings", {})
    return {case_id: entry.get("vector", []) for case_id, entry in embeddings.items() if entry.get("vector")}


def reload_cases() -> None:
    """テスト・データ更新後にキャッシュをクリアする。"""
    _load_cases.cache_clear()
    _load_embeddings.cache_clear()


def case_text_for_embedding(case: dict) -> str:
    """事例を埋め込みベクトル化する際の結合テキスト。

    scripts/build_precedent_embeddings.py と find_relevant_precedents_by_embedding()
    の双方から使う（クエリ側と同じ観点のテキストで埋め込むため）。
    """
    return " ".join([
        case.get("basis_laws", ""),
        case.get("summary", ""),
        case.get("council_name", ""),
        case.get("authority", ""),
    ]).strip()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """標準ライブラリ(math)のみでコサイン類似度を計算する（numpy不使用）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_query(query_text: str) -> Optional[list[float]]:
    """Gemini Embeddings APIでクエリ文字列を埋め込む。失敗時はNoneを返す。"""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
    except ImportError:
        return None
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=query_text,
        )
        embeddings = getattr(response, "embeddings", None)
        if not embeddings:
            return None
        return list(embeddings[0].values)
    except Exception as e:
        print(f"Gemini embed_content error: {e}, falling back to Ngram search")
        return None


def find_relevant_precedents_by_embedding(
    ordinance_name: str,
    alleged_ground: str,
    authority: str = "",
    top_k: int = 3,
    min_similarity: float = 0.6,
) -> list[dict]:
    """Gemini Embeddingsによるコサイン類似度検索で関連度の高い認容事例を探す。

    事前計算済みの埋め込み（data/gyofuku_cases_embeddings.json）が存在せず、
    もしくはGEMINI_API_KEY未設定・API呼び出し失敗の場合は、Ngramヒューリスティック
    検索（find_relevant_precedents）に自動フォールバックする。
    """
    cases = _load_cases()
    if not cases:
        return []

    case_vectors = _load_embeddings()
    if not case_vectors:
        return find_relevant_precedents(ordinance_name, alleged_ground, authority, top_k=top_k)

    query_text = f"{ordinance_name} {alleged_ground} {authority}".strip()
    if not query_text:
        return []

    query_vector = _embed_query(query_text)
    if query_vector is None:
        return find_relevant_precedents(ordinance_name, alleged_ground, authority, top_k=top_k)

    scored = []
    for c in cases:
        vector = case_vectors.get(c.get("case_id", ""))
        if not vector:
            continue
        similarity = _cosine_similarity(query_vector, vector)
        # 同一自治体・機関名が一致していれば加点（Ngram版と同様の趣旨）
        if authority and authority in c.get("authority", ""):
            similarity += 0.05
        if similarity >= min_similarity:
            scored.append((similarity, c))

    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _score, c in scored[:top_k]]


def list_cases() -> list[dict]:
    return [_enrich_case(c) for c in _load_cases()]


def get_case(case_id: str) -> Optional[dict]:
    for c in _load_cases():
        if c.get("case_id") == case_id:
            return _enrich_case(c)
    return None


def search_cases(
    query: str = "",
    category: str = "",
    result: str = "",
    agency_type: str = "",
    tag: str = "",
    limit: int = 50,
) -> list[dict]:
    """一覧・検索ページ用の簡易フィルタ検索。"""
    cases = [_enrich_case(c) for c in _load_cases()]
    out = []
    for c in cases:
        if category and c.get("category") != category:
            continue
        if result and result not in c.get("result", ""):
            continue
        if agency_type and c.get("agency_type") != agency_type:
            continue
        if tag and tag not in c.get("tags", []):
            continue
        if query:
            haystack = " ".join([
                c.get("authority", ""),
                c.get("basis_laws", ""),
                c.get("summary", ""),
                c.get("council_name", ""),
            ])
            if query not in haystack:
                continue
        out.append(c)
    # 裁決日/答申日の新しい順
    out.sort(key=lambda c: c.get("decision_date", ""), reverse=True)
    return out[:limit]


def find_relevant_precedents(
    ordinance_name: str,
    alleged_ground: str,
    authority: str = "",
    top_k: int = 3,
    min_score: int = 4,
) -> list[dict]:
    """条例名・不開示理由に近い認容事例を蓄積データから検索する。

    スコアリングは軽量なNgram一致数によるヒューリスティック。専用の検索エンジンや
    埋め込みベクトル検索ではないため精度は限定的だが、Geminiによる事例の「創作」を
    避け、実在する裁決・答申のみを提示することを目的とする。
    """
    cases = _load_cases()
    if not cases:
        return []

    query_text = f"{ordinance_name} {alleged_ground} {authority}"
    query_tokens = _tokenize(query_text)
    if not query_tokens:
        return []

    scored = []
    for c in cases:
        case_text = f"{c.get('basis_laws', '')} {c.get('summary', '')} {c.get('council_name', '')}"
        case_tokens = _tokenize(case_text)
        score = len(query_tokens & case_tokens)
        # 同一自治体・機関名が一致していれば加点
        if authority and authority in c.get("authority", ""):
            score += 5
        if score >= min_score:
            scored.append((score, c))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _score, c in scored[:top_k]]


def format_precedent_case_for_prompt(case: dict) -> str:
    """precedent_cases（文字列リスト）用の1行フォーマット。"""
    return (
        f"{case.get('authority', '')}（{case.get('decision_date', '不明')}・"
        f"{case.get('result', '')}）: {case.get('basis_laws', '')} — "
        f"{case.get('summary', '')[:80]}… "
        f"[{case.get('attribution', '')}]"
    )
