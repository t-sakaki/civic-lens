"""Civic Lens — 条例・対象機関データ

対象機関（自治体・警察本部）は data/authorities/*.json に1機関1ファイルで保持し、
起動時に読み込んで検証する。新しい機関を追加したい場合は、このディレクトリに
JSONファイルを1つ追加するだけでよく、Pythonコードの変更は不要。

ハッカソン用にコンパクトに、本番では条例全文をCloud Storageに格納してRAG。
"""
import json
import math
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

DATA_DIR = Path(__file__).resolve().parent / "data" / "authorities"


class DisclosureGround(BaseModel):
    """不開示事由（条例第◯条第◯号）"""
    number: str  # "第2号"
    name: str    # "法人情報"
    description: str
    exception: str = ""  # 公益性例外の条文


class OfficeInfo(BaseModel):
    """窓口・アクセス情報"""
    name: str
    address: str
    lat: float
    lon: float
    nearest_station: str


class AuthorityInfo(BaseModel):
    """対象機関情報（条例情報 + 窓口情報 + 自動判定エイリアス）"""
    key: str                # "anjo-city"
    category: str           # "自治体" / "警察"
    authority: str          # "安城市"
    authority_type: str     # "市長"/"市議会"/"県知事" 等
    ordinance_name: str     # "安城市情報公開条例"
    ordinance_id: str       # ファイル番号
    enacted: str            # 制定日
    request_form: str       # 請求書様式
    request_deadline_days: int  # 処分庁の開示決定期間（原則）
    extension_days: int     # 延長可能日数
    review_period_days: int # 審査請求期間（不開示決定後）
    non_disclosure_grounds: List[DisclosureGround]
    review_authority: str   # 審査会（諮問先）
    contact: str            # 窓口
    office: OfficeInfo
    aliases: List[str] = []  # 市民の自然言語入力から自動判定するためのキーワード


# 後方互換: 既存コードは OrdinanceInfo という名前で条例情報を参照している
OrdinanceInfo = AuthorityInfo


def _load_authorities() -> Dict[str, AuthorityInfo]:
    """data/authorities/*.json を読み込み、キー重複などを検証する"""
    authorities: Dict[str, AuthorityInfo] = {}
    if not DATA_DIR.exists():
        return authorities

    for path in sorted(DATA_DIR.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        info = AuthorityInfo(**raw)
        if info.key != path.stem:
            raise ValueError(
                f"{path.name}: JSON内の key ('{info.key}') とファイル名が一致していません"
            )
        if info.key in authorities:
            raise ValueError(f"対象機関キーが重複しています: {info.key}")
        authorities[info.key] = info

    return authorities


AUTHORITIES: Dict[str, AuthorityInfo] = _load_authorities()

# 後方互換: 自治体 / 警察 / 裁判所 で分けた辞書ビュー（既存コードが参照している）
ORDINANCES: Dict[str, AuthorityInfo] = {
    k: v for k, v in AUTHORITIES.items() if v.category == "自治体"
}
POLICE_AUTHORITIES: Dict[str, AuthorityInfo] = {
    k: v for k, v in AUTHORITIES.items() if v.category == "警察"
}
COURT_AUTHORITIES: Dict[str, AuthorityInfo] = {
    k: v for k, v in AUTHORITIES.items() if v.category == "裁判所"
}


POOL_KEY_PREFIX = "pool:"


def _authority_info_from_pool(pooled: Optional[dict]) -> Optional[AuthorityInfo]:
    """municipality_pool（Firestore）の調査済みレコードから AuthorityInfo を組み立てる

    バックグラウンドエージェントが調査した自治体（data/authorities/*.json 未収録）を、
    静的データの自治体と同じインターフェースで扱えるようにする。正式な条例番号・制定日・
    最寄り駅等はまだ調査していないため「自動調査のため不明」のプレースホルダとする。
    """
    if not pooled or pooled.get("status") != "ready":
        return None

    municipality = pooled["municipality"]
    grounds = [DisclosureGround(**g) for g in pooled.get("non_disclosure_grounds", [])]

    return AuthorityInfo(
        key=f"{POOL_KEY_PREFIX}{pooled['muni_code']}",
        category="自治体",
        authority=municipality,
        authority_type=pooled.get("authority_type") or "市長",
        ordinance_name=pooled.get("ordinance_name") or f"{municipality}情報公開条例",
        ordinance_id="（自動調査のため不明）",
        enacted="（自動調査のため不明）",
        request_form=f"{municipality}情報公開請求書",
        request_deadline_days=pooled.get("request_deadline_days") or 30,
        extension_days=pooled.get("extension_days") or 30,
        review_period_days=pooled.get("review_period_days") or 90,
        non_disclosure_grounds=grounds,
        review_authority=pooled.get("review_authority") or f"{municipality}情報公開審査会",
        contact=pooled.get("contact") or f"{municipality}役所",
        office=OfficeInfo(
            name=f"{municipality}役所",
            address=pooled.get("full_name") or municipality,
            lat=pooled.get("lat") or 0.0,
            lon=pooled.get("lon") or 0.0,
            nearest_station="不明（自動調査のため未特定）",
        ),
        aliases=[municipality],
    )


def get_ordinance(authority_key: str) -> Optional[AuthorityInfo]:
    """条例・取扱要綱を取得（自治体・警察・裁判所すべて）

    "pool:<muni_code>" 形式のキーは、Geolocationのバックグラウンド調査で
    municipality_pool に保存された未収録自治体を指す。
    """
    if authority_key and authority_key.startswith(POOL_KEY_PREFIX):
        from municipality_pool import get_pooled
        muni_code = authority_key[len(POOL_KEY_PREFIX):]
        try:
            pooled = get_pooled(muni_code)
        except Exception:
            return None
        return _authority_info_from_pool(pooled)

    return AUTHORITIES.get(authority_key)


def list_authorities() -> List[str]:
    """対応機関一覧（自治体・警察・裁判所すべて）"""
    return list(AUTHORITIES.keys())


def is_police_authority(authority_key: str) -> bool:
    """警察機関かどうか"""
    return authority_key in POLICE_AUTHORITIES


def is_court_authority(authority_key: str) -> bool:
    """裁判所機関かどうか"""
    return authority_key in COURT_AUTHORITIES


def get_office_info(authority_key: str) -> Dict:
    """対象機関の窓口・アクセス情報（station_guide.py から利用）"""
    info = AUTHORITIES.get(authority_key) or AUTHORITIES.get("anjo-city")
    return info.office.model_dump()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の距離（km）をハーバーサイン公式で概算する"""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def list_nearby_authorities(
    lat: float, lon: float, radius_km: float = 40.0, max_results: int = 8
) -> List[Dict]:
    """現在地の近くにある対象機関を距離順に列挙する

    自治体は1つに絞り込まず、都道府県（県庁等）や周辺の市区町村・警察・裁判所を
    候補として複数返す。半径内に候補が1件もない場合は、最も近い1件だけを返す。
    """
    ranked = sorted(
        (
            {
                "key": key,
                "name": info.authority,
                "type": info.authority_type,
                "category": info.category,
                "distance_km": round(_haversine_km(lat, lon, info.office.lat, info.office.lon), 1),
            }
            for key, info in AUTHORITIES.items()
        ),
        key=lambda c: c["distance_km"],
    )

    within_radius = [c for c in ranked if c["distance_km"] <= radius_km]
    if within_radius:
        return within_radius[:max_results]
    return ranked[:1]


def match_authority_by_text(text: str, default: str = "anjo-city") -> str:
    """市民の自然言語入力に含まれるエイリアスから対象機関キーを推定する

    複数の機関のエイリアスが部分文字列として重なる場合（例: 「愛知県」は
    「愛知県警」にも含まれる）があるため、より長いエイリアスから優先的に
    マッチさせることで誤判定を防ぐ。
    """
    candidates = [
        (alias, info.key)
        for info in AUTHORITIES.values()
        for alias in info.aliases
    ]
    candidates.sort(key=lambda pair: len(pair[0]), reverse=True)

    for alias, key in candidates:
        if alias in text:
            return key

    return default


# ======================================================================
# 警察特有・反論ロジック追加
# ======================================================================
POLICE_COUNTER_ARGUMENTS = {
    "第5条第3号": [
        "「捜査に支障」は抽象的では足りず、具体的・個別的な支障が必要。事件",
        "既に終結した事件の情報は開示すべき時期にきている。",
        "統計情報・統計値は捜査の支障とはならない。",
        "犯人識別情報等は黒塗り（マスキング）で部分開示が相当。",
        "既に公判で公開された情報は捜査情報の対象外。",
    ],
    "第5条第4号": [
        "警備情報は時限性があり、一定期間経過後は開示可能。",
        "統計的な事案数・検挙数等は公共の安全に支障しない。",
        "公共の安全と秩序の維持は、抽象的不安では足りない。",
        "テロ対策手法自体ではなく、その運用実績等は開示可能。",
    ],
    "第5条第5号": [
        "警察の事務であっても意思決定後の情報は開示すべき。",
        "人事・予算・契約情報等は通常の行政情報と同じ取扱い。",
        "内部通達・運用要領等は政策判断の基礎情報として公益性が高い。",
    ],
}


# 不開示事由の典型的反論ロジック（自治体用）
COMMON_COUNTER_ARGUMENTS = {
    "第7条第2号": [
        "「権利、競争上の地位その他正当な利益を害するおそれ」は、抽象的・主観的な理由ではなく、具体的・実質的な害益のおそれでなければならない（最判平14.2.8）。",
        "当該情報が公になっても、法人等の正当な利益が実際に害される具体的な根拠を行政は示す責任がある。",
        "「競争上の地位」は事業者間の競争関係を念頭に置くものであり、本件のような内部資料には該当しない。",
        "既に公になっている情報と同内容の情報は、法人情報に該当しない。",
        "法人等の代表者個人の意見・判断に関する情報で、開示しても法人の正当な利益を害しないものは除く。",
    ],
    "第7条第3号": [
        "審議・検討中の情報は、意思決定後の情報については原則開示すべき（最判平11.12.16）。",
        "「率直な意見の交換が不当に損なわれるおそれ」は、単なる可能性では足りず、具体的・実質的なおそれが必要。",
        "本件は既に決定された事項であり、審議中の情報ではない。",
        "本件情報は事実経過の記録であり、意見・判断そのものではない。",
    ],
    "第7条第4号": [
        "「事務の適正な遂行に支障を及ぼすおそれ」は、抽象的・一般的不安では足りず、具体的・個別的な支障が必要。",
        "同種の情報は他の自治体で開示されている実例があり、支障は認められない。",
        "事務の性質は時系列的に変化し、意思決定後の情報は開示すべき時期に来ている。",
        "本件情報は統計的・客観的数値であり、事務執行に影響する性質のものではない。",
    ],
}


# ======================================================================
# 裁判所（司法行政文書取扱要綱）特有・反論ロジック
# ======================================================================
COURT_COUNTER_ARGUMENTS = {
    "第4条第1号": [
        "公務員の職務遂行に係る情報（氏名・役職・職務内容等）は個人のプライバシー侵害にあたらず開示すべき。",
        "特定の個人を識別できる部分がある場合でも、黒塗り（マスキング）による部分開示を行うべきである。",
        "裁判所職員・裁判官の公務遂行の透明性を確保するため、公益性に基づく開示が相当である。",
    ],
    "第4条第2号": [
        "「法人等の正当な利益を害するおそれ」は抽象的な理由では足りず、具体的・客観的な支障の立証が必要である。",
        "契約金額、入札結果、仕様書等は公金支出の適正性を担保する基礎情報であり、正当な利益を害しない。",
        "既に公にされている情報と同内容の情報は法人情報に該当しない。",
    ],
    "第4条第3号": [
        "意思決定が終了した事案に係る審議・検討文書は、事後的に検証を可能とするため原則開示すべきである。",
        "「率直な意見の交換が損なわれるおそれ」は単なる主観的懸念ではなく、将来の審議への具体的支障を要する。",
        "検討過程における客観的事実の記録や基礎データは、意見・判断そのものと分離して開示可能である。",
    ],
    "第4条第4号": [
        "本件請求文書は個別事件の訴訟記録ではなく、司法行政上の制度運用基準・統計・会計文書であり、裁判の公正・独立に影響を及ぼすものではない。",
        "「事務の適正な遂行に著しい支障を及ぼすおそれ」は具体的かつ実質的な支障を要し、事務負担の増大のみを理由とする不開示は違法・不当である。",
        "同種の司法行政文書（事務処理要領や通達等）は他庁・他裁判所でも開示実績があり、支障は認められない。",
        "裁判所の組織運営および公費執行の透明性を高めることは、司法に対する国民の信頼向上に資する。",
    ],
    "第4条第5号": [
        "庁舎の一般的な案内図面や過去の警備実績・契約内容は、直ちに警備上の重大な支障を招くものではない。",
        "公共の安全に対する具体的危険が生じる部分に限り部分マスキングを行い、その他は開示すべきである。",
    ],
}

